# Analyse — Autoriser des utilisateurs de l'allow-list (pas seulement l'owner) sur Slack

Branche : `feature/slack-allowlist-invocation`

## Constat principal

L'infrastructure d'allow-list **existe déjà** (UI Allow/Deny, persistance config,
état runtime `_allowed_users`), mais l'accès multi-utilisateur est **désactivé
volontairement** à deux endroits indépendants. Ce n'est pas un manque technique,
c'est un verrou de sécurité à ouvrir.

## Les deux verrous

### Verrou 1 — le prédicat d'autorisation (chemin natif Slack)
`src/kiro_crew/slack/handler_runtime/access.py` → `is_allowed_user()`

```python
def is_allowed_user(user_id: str) -> bool:
    """Multi-user access is disabled for security — only the owner is authorized."""
    if not user_id:
        return False
    return is_owner(user_id)   # <-- ignore totalement _allowed_users
```

Et `is_open_channel()` renvoie `return False` en dur.

Ce prédicat est le point de contrôle de TOUTES les entrées Slack natives :
- `inbound.py:343`, `commands.py:575`, `approvals.py:461`, `interactions.py:420/517/706`,
  `events.py:837`, `handler.py:1026`.

### Verrou 2 — le peuplement de `_allowed_users`
`src/kiro_crew/slack/gateway.py:964-970`

```python
# Multi-user access is disabled — only owner is authorized.
stale = {u["slack_id"] for u in cfg.slack.allowed_users} - ({self._owner_id} if ... )
if stale:
    logger.warning("Pruning %d stale allowlist entries (multi-user disabled)")
self._allowed_users = {self._owner_id} if self._owner_id else set()
```

Même si on corrigeait le verrou 1, l'ensemble `_allowed_users` est écrasé au boot
pour ne contenir que l'owner (les entrées config sont *pruned*).
`events.py:1049` partage ensuite cet ensemble owner-only aux handlers.

## Ce qui existe déjà et est réutilisable

- `src/kiro_crew/slack/allowlist.py` : prompts Allow/Deny vers l'owner,
  `persist_allowed_user()` / `persist_tracking_channel()` → écrit
  `config.json → slack.allowed_users` (verrouillé via `update_config_locked`).
- `config.slack.allowed_users` : liste de `{slack_id, name}` déjà dans le schéma config.
- `access.set_allowed_users()` : installe l'ensemble runtime (déjà appelé au boot).
- Les action-ids `allowlist_approve` / `allowlist_deny` et leur routage dans
  `interactions.py`.
- Le **transport messaging** (`transport.py:90/155/160`) honore DÉJÀ un ensemble
  multi-utilisateur (`self._allowed_users` = frozenset) — seul le chemin natif
  verrouille à l'owner.

## Changements requis (minimal, pour rouvrir proprement)

1. **`access.is_allowed_user`** : rétablir la vraie vérification
   `return is_owner(user_id) or user_id in _allowed_users`.
2. **`gateway.py`** : ne plus pruner — peupler `_allowed_users` avec
   `{owner} ∪ {u.slack_id for u in cfg.slack.allowed_users}`.
3. (Optionnel) **`is_open_channel`** : honorer `_open_channels` si on veut aussi
   des canaux ouverts ; sinon le laisser fermé.
4. Garder l'owner comme SEUL approbateur des ajouts (prompts Allow/Deny
   déjà en place) — on n'ouvre pas l'auto-inscription.

## Points de sécurité à préserver

- Les commandes **owner-only** (YOLO, trust, track channel, dashboard link)
  doivent rester gardées par `is_owner()`, pas par `is_allowed_user()`.
  Vérifier chaque call-site : certains utilisent déjà `is_owner(...) or is_allowed_user(...)`
  (events.py:837, handler.py:1026, commands.py:1265/1324) — à auditer un par un
  pour décider owner-only vs allow-list.
- `send_dashboard_link` : attention, un lien dashboard présigné = session complète.
  Décider s'il reste owner-only.
- Les approbations d'outils (`approvals.py`) : un utilisateur de l'allow-list
  peut-il approuver l'exécution d'outils ? Probablement à restreindre.

## Tests existants à faire tourner

`test/` contient des tests Slack (allowlist, access). À cibler plutôt que la
suite complète (mémoire hôte tendue).

## Décisions (2026-10-09, Jérôme)

1. **Approbations d'outils — BY DESIGN.** Un membre de l'allow-list PEUT approuver
   l'exécution d'outils sur SES PROPRES threads. Ce n'est pas restreint : c'est
   le comportement voulu. Garde-fou conservé : `approvals.py` exige que le
   cliqueur possède le thread (`thread_owner == user_id`), donc un membre ne peut
   pas approuver le thread d'un autre.
2. **Audit call-site — FAIT, aucun écart.** Résultat ci-dessous.
3. Tests dédiés à la capacité — à écrire.
4. Doc du flux d'ajout — ci-dessous.

## Résultat d'audit des call-sites

Tous les call-sites `is_owner(uid) or is_allowed_user(uid)` sont des commandes
SELF-SERVICE, correctement ouvertes à un membre :
- `sessions` (lister SES sessions) — events.py:837, handler.py:1026
- `!compact` — commands.py:1265
- `!dashboard`, `!stop`, `!title` — commands.py:1324 (lien dashboard DMé au
  demandeur = SA propre session, pas d'escalade)

Toutes les AUTRES commandes `!` tombent dans `elif not is_owner(user_id)` →
« ⛔ Owner-only command » (commands.py). YOLO global, trust global et les
réglages de tracking restent donc strictement owner. **L'allow-list n'élargit
aucune commande sensible.**

## Flux d'ajout d'un membre à l'allow-list

Deux chemins convergent sur la MÊME liste `config.json → slack.allowed_users` :

1. **Depuis l'UI** (nouveau) : Settings → Slack → Identity & access → éditeur
   « Allowed users ». L'owner saisit un Slack member ID (`U…`/`W…`). Save →
   `PUT /api/slack/config` → persiste + ré-applique en live (pas de restart).
2. **Depuis Slack** : quand un utilisateur rejoint un canal tracké, ou via
   `/kirocrew @user`, l'owner reçoit un DM Allow/Deny (`slack/allowlist.py`).
   Un clic « Allow » appelle `persist_allowed_user()` + `set_allowed_users()`
   (interactions.py:2426-2427), qui met à jour la même liste runtime.

Les deux écrivent `{slack_id, name}`. L'UI n'édite que les IDs ; les `name`
posés par le prompt Allow/Deny sont préservés au save (voir slack_settings.py).
L'owner (`KIROCREW_OWNER_ID`) est toujours autorisé, qu'il figure ou non dans
la liste.
