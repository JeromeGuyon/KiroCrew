# Slack multi-workspace — design & audit

**Date:** 2026-10-09
**Branche:** `feature/slack-multi-workspace` (worktree `kc-slack-multiws`), basée sur `feature/slack-allowlist-invocation`
**Objectif:** permettre à une seule gateway de se connecter à N workspaces Slack, chacun avec son bot/app token, son owner, ses allow-lists et ses canaux — sans casser l'install mono-workspace.

**Périmètre confirmé (Jérôme, 2026-10-09):** les workspaces cibles sont **indépendants** — ils ne font PAS partie d'une même organisation Enterprise Grid. Donc mapping **1:1 `team_id → account_id`** (pas 1:N), chaque workspace a sa propre install d'app (tokens distincts) et son propre owner naturellement. Rappel des termes : `team_id` = un workspace ; `enterprise_id` = une org Grid regroupant plusieurs workspaces (NON applicable ici).

---

## Résultat de l'audit (étape 0) — faits vérifiés

### Ce qui est MONO aujourd'hui (à multiplier)
- `gateway.py` (`__init__`): un seul triplet de credentials, lu une fois :
  - `self._app_token = creds.get(CRED_SLACK_APP_TOKEN)`
  - `self._bot_token = creds.get(CRED_SLACK_BOT_TOKEN)`
  - `self._owner_id  = creds.get(CRED_OWNER_ID)`
  - `self._slack_enabled = bool(app_token and bot_token)`
- `self._socket_client: WSSocketModeClient | None` — **une seule** connexion Socket Mode.
- `self._allowed_users`, `self._tracking_channels`, `self._open_channels` — un seul jeu, dérivé du `SlackConfig` unique (sections.py:2171).
- **Asymétrie clé:** tous les autres canaux ont une méthode de montage `_hoist_<channel>` (`_hoist_telegram`, `_hoist_wecom`, …) appelée en boucle. **Slack n'a PAS de `_hoist_slack`** — il est câblé en dur. C'est le point d'entrée de la refonte.

### Ce qui est DÉJÀ prêt (bonne nouvelle — réduit le risque)
- **Identité du workspace entrant déjà extraite et validée.** `enterprise.py` (module dédié) valide `team_id`/`enterprise_id`, cache les team_ids acceptés, expose `check_message_origin()`.
- **Routage par team_id déjà amorcé.** `events.py` (~1196) : « envelope team_id is the bot's workspace », préfère toujours le `team_id` d'enveloppe (anti-bypass cross-workspace), rejette un message sans team_id. `client.py:310-327` cache **channel→team_id** (`record_channel_team` / `ensure_channel_team`) et auto-injecte le team_id.
- **Le team_id circule déjà** dans toute la chaîne : `handler.py` (`team_id=`), `stream.py` (`self.team_id`), `interactions.py:622` (`payload["team"]["id"]`). Donc chaque event SAIT déjà de quel workspace il vient.
- **Précédent de credentials per-instance suffixés.** `loader.py` : `JIRA_TOKEN_<HEX>` via `jira_host_token_name(host)` — KiroCrew sait déjà résoudre un credential par instance. Modèle direct pour `SLACK_BOT_TOKEN__<account_id>`.
- **Précédent de config multi-comptes.** `telegram.accounts` (`_parse_telegram_accounts`) : map `{account_id → {bot_token, …}}`, parsing fail-safe, round-trip préservé. Modèle direct pour `slack.accounts`.

### Conclusion d'audit
La faisabilité est **confirmée et le risque réduit** : l'identité de workspace est déjà portée et validée de bout en bout. L'essentiel du travail est de (a) multiplier la connexion socket + les credentials, et (b) rendre l'autorisation et les sessions **relatives au compte**. Le routage entrant est en grande partie déjà là.

---

## Design retenu : `slack.accounts`

Map `{account_id → SlackAccountConfig}`. Le triplet top-level actuel devient le compte implicite `default` (rétrocompat totale).

```
SlackAccountConfig:
  bot_token: str          # credential, résolu via SLACK_BOT_TOKEN__<account_id>
  app_token: str          # credential, résolu via SLACK_APP_TOKEN__<account_id>
  owner_id: str           # owner PROPRE à ce workspace
  allowed_users: list[{slack_id, name}]
  tracking_channels: list[{channel_id, name}]
  open_channels: list[str]
  allowed_enterprise_ids: list[str]
  session_folder: str
  command: str            # slash command (per-install)
```

`team_id` n'est pas stocké en config : il est résolu au boot via `auth.test` sur chaque compte, puis sert de clé de routage entrant.

---

## Phasage

### Phase 1 — Config & modèle (sans comportement)
- `SlackAccountConfig` dataclass (sections.py).
- `SlackConfig.accounts: dict[str, SlackAccountConfig]`.
- `_parse_slack_accounts` calqué sur `_parse_telegram_accounts` (fail-safe, round-trip).
- Rétrocompat: `accounts` vide ⇒ compte `default` synthétisé depuis le top-level. Install actuelle inchangée.
- Tests: round-trip config, synthèse `default`.

### Phase 2 — Credentials multi-secret
- Resolver `slack_account_token_name(account_id, kind)` → `SLACK_BOT_TOKEN__<account_id>` / `SLACK_APP_TOKEN__<account_id>`, sur le modèle `JIRA_TOKEN_<HEX>`.
- Keychain: N jeux de clés. Vérifier le scrub sandbox (`_AGENT_DENIED_ENV_KEYS`) couvre les nouvelles clés.
- Tests: résolution per-account, absence silencieuse d'un compte mal configuré.

### Phase 3 — Connexions multiples (`_hoist_slack`)

> **RÉVISION POST-AUDIT (2026-10-09) — cohérence d'ensemble.**
> Phases 1 et 2 **livrées et testées** (commits `475a4d4d0`, `50be3fa15`).
> L'audit du cœur du gateway révèle le point structurant que le plan initial
> sous-estimait : **toute l'autorisation Slack repose sur des GLOBALS DE MODULE**
> dans `handler_runtime/access.py` (`_allowed_users`, `_owner_id`, `_orch_cfg`,
> `_dashboard_state`), posés UNE SEULE FOIS au boot via `set_allowed_users()` /
> `set_owner_id()` / `set_orch_cfg()` depuis `events.py`. Le listener `_on_event`
> (events.py:1075+) fait tout en s'appuyant dessus : ACK, check owner, YOLO,
> validation enterprise, routage.
>
> **Conséquence sur l'ordre.** On NE PEUT PAS monter N sockets avant de rendre
> ces globals indexés par compte : sinon le dernier compte monté écraserait
> l'owner/allow-list des autres → élévation cross-workspace silencieuse. La
> Phase 5 (isolation d'autorisation) est donc un **prérequis** de la Phase 3,
> pas une suite. Réordonnancement :
>
> - **3a (fondation, sûre, additive).** Construire au boot
>   `self._slack_runtime: dict[account_id, {bot_token, app_token, owner_id, team_id}]`
>   via `resolve_slack_account()` pour `default` + chaque `accounts`. Résoudre le
>   `team_id` de chaque compte par `auth.test` (off-loop). AUCUN changement du
>   socket unique existant : le `default` reste câblé exactement comme aujourd'hui.
>   Testable isolément, zéro régression runtime.
> - **3b (= ancienne Phase 5, FAIT AVANT les sockets).** Transformer les globals
>   d'`access.py` en registre par compte : `is_owner(uid, account_id)` /
>   `is_allowed_user(uid, account_id)` lisent la map du compte. Garder une
>   compat `default` pour les call-sites non encore paramétrés. Tests
>   cross-workspace obligatoires (owner A rejeté sur compte B).
> - **3c (sockets multiples).** Extraire `_on_event` en **factory**
>   `make_on_event(account_id)` capturant les credentials/validateurs du compte ;
>   remplacer `self._socket_client` par `dict[account_id, WSSocketModeClient]` ;
>   un socket par compte avec app_token. Le `default` emprunte désormais le même
>   chemin que les autres (dogfood du path multi-compte).
> - **3d (routage entrant, = ancienne Phase 4).** `team_id` d'enveloppe →
>   `account_id` (fail-closed si inconnu). Propager `account_id` dans

(voir aussi les décisions « pas de changement nécessaire » ci-dessous)

## Décisions d'investigation (2026-10-09) — pas de changement nécessaire

Deux items du plan se sont révélés inutiles après vérification du code — documentés
ici pour qu'on ne les ré-ouvre pas :

1. **Session key `(account_id, channel, thread_ts)` — NON nécessaire.** Les IDs
   Slack de canal/DM (`C…`/`D…`) sont **globalement uniques** : un même ID ne peut
   pas appartenir à deux workspaces, et un `thread_ts`/`msg_ts` est unique dans son
   canal. La clé actuelle (`flat_dm_session_key(channel, …)` ou `thread_ts`/`msg_ts`)
   ne peut donc PAS entrer en collision entre workspaces. Ajouter `account_id` à la
   clé n'apporterait rien et ferait courir un risque réel : chaque site qui construit
   ou parse la clé (stop, dequeue, dashboard, dispatch) devrait matcher, et une
   incohérence laisserait des sessions orphelines. Laissé tel quel délibérément.

2. **Prompt owner par compte (`_maybe_prompt_owner`) — rien à faire.** Dans ce build,
   `_maybe_prompt_owner` est déjà un **no-op** : les prompts Allow/Deny à l'arrivée
   d'un membre dans un canal tracké sont désactivés (multi-user channel-join
   disabled). Il n'y a donc aucune autorité `default` à rendre account-aware ; le
   chemin est mort. (Si ces prompts étaient réactivés un jour, il faudrait alors leur
   passer l'`account_id` du socket, comme pour `is_allowed_user`.)

>   handler/stream/interactions. Session key = `(account_id, channel, thread_ts)`.
>
> Les anciennes Phases 4 et 5 ci-dessous sont ABSORBÉES par 3b/3d ; conservées
> pour mémoire. Phases 6 (UI) et 7 (manifest/doc) inchangées, après 3d.

- Introduire `_hoist_slack(cfg, creds)` sur le modèle des autres canaux.
- Remplacer `self._socket_client` unique par `self._slack_sockets: dict[account_id, WSSocketModeClient]`.
- Démarrer un socket par compte activé (app_token présent). Au boot, `auth.test` → mapper `team_id → account_id`.
- Indexer les clients WebClient par account_id ; chaque réponse sortante utilise le bot_token du compte résolu.
- Tests: 2 comptes montent 2 sockets ; un compte sans token est sauté.

### Phase 4 — Routage entrant par compte
- À la réception, résoudre `account_id` depuis le `team_id` d'enveloppe (déjà extrait par `events.py`). Rejeter si inconnu (fail-closed, déjà le comportement sur team_id manquant).
- Propager `account_id` dans tout le pipeline (handler/stream/interactions) à côté du `team_id` déjà transporté.
- Session key = `(account_id, channel, thread_ts)` pour isoler les workspaces.

### Phase 5 — Autorisation per-workspace (POINT SÉCURITÉ CRITIQUE)
- `is_owner(uid)` / `is_allowed_user(uid)` deviennent **relatifs au compte** : l'owner du workspace A n'a aucune autorité sur le workspace B.
- `_allowed_users`, `_tracking_channels`, `_open_channels` deviennent des maps par account_id.
- Garde-fou: un bug ici = élévation cross-workspace. Tests dédiés obligatoires : owner A rejeté sur events du compte B ; allow-list A sans effet sur B.

### Phase 6 — UI dashboard
- Settings → Slack : liste de comptes (ajouter/supprimer un workspace, chacun ses tokens + owner + allow-list + canaux). Réutiliser le pattern `TagListEditor` et le per-account de la page Telegram si présent.

### Phase 7 — Manifest, commandes, doc
- Chaque workspace installe l'app avec son manifest/scopes propres.
- Documenter le flux d'ajout d'un 2ᵉ workspace (install app → tokens → account_id → owner).

---

## Risques classés
1. **Isolation d'autorisation (critique).** Fail-closed par défaut, tests cross-workspace obligatoires. Un owner d'un workspace ne doit JAMAIS piloter la machine via un autre.
2. **Routage team_id.** Vérifier que events + interactions + shortcuts + slash exposent tous le team_id d'enveloppe. (Events: OK d'après l'audit. Interactions: `payload["team"]["id"]` présent. Slash/shortcuts: à confirmer en Phase 4.)
3. **Enterprise Grid — HORS PÉRIMÈTRE.** Les workspaces cibles sont indépendants (confirmé). Mapping 1:1 `team_id→account_id`, pas de regroupement org. `enterprise.py` continue de valider le team_id par compte ; aucun mapping org→N team_id à gérer. (À ne rouvrir que si un workspace Grid était ajouté plus tard.)
4. **Rétrocompat.** Le chemin `default` ne doit jamais régresser l'install mono-token.
5. **Keychain/sandbox.** Les nouvelles clés doivent être scrubées des env agent comme les tokens actuels.

## Étape suivante proposée
Implémenter Phase 1 (config + rétrocompat `default` + tests) — zéro changement de comportement, socle du reste.
