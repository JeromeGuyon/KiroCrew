# Hermes-Agent vs KiroCrew — Slack implementation feature-gap report

**Date:** 2026-10-09
**Author:** kirocrew-research
**Scope:** Slack platform/adapter code only.

## Sources

**Hermes** (NousResearch/hermes-agent, `main`, light sparse+blobless clone):
- `plugins/platforms/slack/adapter.py` (6943 lines), `block_kit.py`, `plugin.yaml`
- `hermes_cli/subcommands/slack.py` (manifest generator CLI)
- `gateway/authz_mixin.py` (738 lines — centralized authorization)
- `website/docs/user-guide/messaging/slack.md` (1100 lines)

**KiroCrew** (read-only):
- `src/kiro_crew/slack/` — `handler.py`, `gateway.py`, `events.py`, `interactions.py`,
  `handler_runtime/{access,commands,approvals}.py`, `allowlist.py`,
  `gateway_runtime/channel_lifecycle.py`
- `docs/analysis/slack-allowlist-invocation.md` (allowlist lock analysis)

Clone succeeded (no network/404 error).

---

## 1. Feature matrix

| Feature | Hermes | KiroCrew | Gap / Notes |
|---|---|---|---|
| **Multi-user / allowlist auth model** | First-class, **enabled**. Env `SLACK_ALLOWED_USERS` (+ `GATEWAY_ALLOWED_USERS`), `*` wildcard, `SLACK_ALLOW_ALL_USERS`/`GATEWAY_ALLOW_ALL_USERS`. Centralized in gateway `_is_user_authorized`; cross-platform alias normalization (WhatsApp JID/LID, Nostr npub→hex). | Infra exists (`config.json → slack.allowed_users`, runtime `_allowed_users`, Allow/Deny prompts) but **deliberately locked to owner-only** at two points: `access.is_allowed_user` effectively returns `is_owner`, and `gateway.py` prunes the set to `{owner}` at boot. | **Hermes allows true multi-user; KiroCrew is owner-only by security choice.** KiroCrew's lock is a toggle, not a missing capability (see allowlist analysis). |
| **Tool-approval flow** | Block Kit prompt, **4 tiers**: Approve once / this session / always / Deny (`hermes_approve_once|session|always`, `hermes_deny`). Buttons call `resolve_gateway_approval()` to unblock the waiting agent thread. Separate 3-way slash-confirm (once/always/cancel). | Block Kit prompt with **Approve** + **Trust** (per-session auto-approve) + Deny. Trust = `add_trusted_session`; also a **late-Trust** button on a running thread (`_grant_late_trust`). Mirrors to linked dashboard cards. | Both have per-session trust. **Hermes has an explicit "always" (durable, cross-session) tier in-band**; KiroCrew's durable grant comes via dashboard Trust, and global auto-approve is YOLO (below). |
| **Owner vs member privileges** | Owner concept is lighter — authorization is allowlist membership; `SLACK_ALLOW_ALL_USERS` opens the workspace. Interactive actions re-check `_is_interactive_user_authorized`. | Strong owner/member split. Owner = `KIROCREW_OWNER_ID` (W/U prefix cross-match). **Owner-only:** YOLO, Trust grants, dashboard-link minting, channel tracking, `restart`. Self-service (owner *or* allowlist): `sessions`, `!compact`, `!dashboard`/`!stop`/`!title` (own session). | **KiroCrew has a finer, audited privilege ladder.** Hermes leans on allowlist-in/out + per-interaction auth. |
| **Channel tracking** | `allowed_channels` (whitelist), `ignored_channels` (denylist), `free_response_channels`, `require_mention_channels`, home channel for cron. All env/YAML. | Tracked-channel set (`_tracking_channels`, `is_tracked_channel`), persisted to config via `!channel` / `/kirocrew channels`; owner Allow/Deny prompt when a user joins a tracked channel. Open channels **hard-disabled** (`is_open_channel → False`). | Different models: Hermes = static config lists; **KiroCrew = runtime-managed tracked set with join-time owner approval**. |
| **Thread handling** | `thread_require_mention`, strict/free threads, thread auto-engagement (once a session is live, follow-ups need no mention), `ignore_other_user_mentions`, silence-token policy per surface. Group DM (MPIM) treated as shared surface. | Thread-reply handling (`thread_replies.py`, `thread_parent.py`); session keyed per thread; Trust/approvals scoped to the thread owner. | **Hermes has a richer declarative thread-gating matrix** (4 interacting keys + MPIM policy). KiroCrew's thread logic is session/trust-centric. |
| **Slash / CLI commands** | Every gateway `COMMAND_REGISTRY` command auto-registered as a **native Slack slash** (`/btw /stop /model /new /help …`) via `hermes slack manifest` generator (parity with Discord/Telegram). | `/kirocrew <sub>` dispatcher: `yolo, config, users, channels, sessions, status, restart, dashboard, agent, voice`; plus `!bang` commands (`!yolo !stop !voice !agent !dashboard !ta !project !allowlist !channel !title`). No manifest-generator CLI. | **Hermes exposes many first-class native slashes + a manifest CLI; KiroCrew multiplexes through one `/kirocrew` slash + bang-commands.** |
| **DM behavior** | 1:1 DM always mention-exempt; `disable_dms` ignores incoming DMs; unauthorized-DM policy; Slack **AI Assistant / Agent view** modes selectable in manifest (`assistant_view` vs `agent_view`). | DM activation gating (`slack_dm_activation`); native path owner/allowlist gated. | **Hermes supports Slack's Assistant/Agent messaging surfaces** (manifest-selectable); not seen in KiroCrew. |
| **Late-trust / YOLO** | "Always approve" tier on the approval prompt (durable per tool/session). No separate global YOLO toggle observed on the Slack surface. | **Global YOLO** (`!yolo on/off/renew`, owner-only, TTL-based via `SafetyOverride`, policy-gated by `yolo_policy_permits`, audit-logged) + **per-session Trust** + **late-Trust** thread button + durable dashboard-linked Trust. | **KiroCrew's YOLO/Trust is more governed** (TTL, org-policy veto, audit, renew). Hermes's "always" is simpler and in-band. |
| **Config surface** | YAML↔env bridge: `slack:` YAML keys → `SLACK_*` env (`require_mention`, `strict_mention`, `thread_require_mention`, `allow_bots`, `reactions`, `disable_dms`, `free_response_channels`, `allowed_channels`, `ignored_channels`, `reaction_triggers`…). Interactive `hermes setup`. | `config.json` (`slack.allowed_users`, `slack_channels`, `slack_dm_activation`) + dashboard **Settings → Slack** UI, live-applied via `PUT /api/slack/config` (no restart). In-place config mutation shared across orchestrator/dispatchers. | **Hermes = file/env + CLI; KiroCrew = dashboard UI + live reload.** Different operator ergonomics. |
| **Reaction triggers** | `reaction_triggers` (route emoji reactions into the agent loop; optional target channel). | Not observed in the surveyed files. | **Hermes-only feature.** |
| **Dashboard integration** | None (CLI/file-driven product). | Deep: dashboard sessions, presigned dashboard links, Trust mirrored to dashboard cards, `_dashboard_state` push-refresh. | **KiroCrew-only; architecturally central.** |

---

## 2. Ranked feature gaps

### A. Hermes does it, KiroCrew does not

1. **Native per-command Slack slash registration + `slack manifest` generator CLI.**
   Hermes exposes `/btw /stop /model /new /help …` as real Slack slashes and ships a
   CLI to emit the app manifest. *Worth porting?* **Medium** — better discoverability/UX,
   but KiroCrew's single `/kirocrew` + bang model is intentional and lower-maintenance.
2. **Reaction triggers** (`reaction_triggers`) — route emoji reactions into the agent.
   *Worth porting?* **Low–Medium** — neat UX affordance, isolated to add; niche.
3. **Slack AI Assistant / Agent view manifest modes** (`assistant_view` vs `agent_view`).
   *Worth porting?* **Medium** — native Slack Assistant pane is a real UX upgrade for DMs;
   non-trivial manifest + event-handling work.
4. **Declarative thread/mention gating matrix** (`require_mention_channels`,
   `ignore_other_user_mentions`, free-response + thread-require-mention interplay, MPIM
   shared-surface policy). *Worth porting?* **Medium** — useful in busy multi-human channels;
   KiroCrew's owner-only model reduces the need today.
5. **True multi-user allowlist + `*` wildcard / allow-all** actually wired through.
   *Worth porting?* **Deliberately declined** — KiroCrew's lock is a security decision
   (see allowlist analysis); the infra to unlock already exists if policy changes.
6. **Cross-platform identity alias normalization** (WhatsApp JID/LID, Nostr npub→hex) in
   the shared authz layer. *Worth porting?* **N/A for Slack** — Slack IDs don't alias;
   KiroCrew already does W↔U owner prefix cross-match.
7. **Bot-to-bot admission** (`SLACK_ALLOW_BOTS`, Workflow Builder posts with `user=None`).
   *Worth porting?* **Low** — only if KiroCrew needs Slack Workflow Builder integration.

### B. KiroCrew does it, Hermes does not

1. **Dashboard integration** (sessions, presigned links, Trust mirrored to dashboard cards,
   live config reload). *Port to Hermes?* **No** — Hermes has no dashboard; architectural.
2. **Governed global YOLO** — owner-only, TTL, org-policy veto (`yolo_policy_permits`),
   audit-logged, renewable. *Worth Hermes porting?* **High value** if Hermes wants
   auditable, bounded auto-approve rather than a plain "always" button.
3. **Late-Trust thread button** — grant trust mid-thread on a running turn. **Medium.**
4. **Runtime-managed tracked-channel set with join-time owner Allow/Deny prompt.**
   **Medium** — more interactive than Hermes's static `allowed_channels`.
5. **Finer owner/member privilege ladder with per-command gating + audit.** **Medium.**

---

## 3. Notable architectural differences

- **Authorization locus.** Hermes centralizes auth in the **gateway** (`authz_mixin.py`,
  `_is_user_authorized`) shared across all platforms, with the Slack adapter delegating via
  injected `set_authorization_check`; it also supports **multiplex profiles** (multiple bot
  identities / profiles behind one gateway, fail-closed per profile). KiroCrew keeps Slack
  auth in the **Slack module** (`handler_runtime/access.py`) as module-level state installed
  by the gateway at boot.
- **Config model.** Hermes = **env-var / YAML file** first (YAML→env bridge) + interactive
  `hermes setup`. KiroCrew = **dashboard UI + config.json** with live in-place reload (no
  restart), one shared config object across orchestrator and dispatchers.
- **Command surface.** Hermes projects a single gateway command registry onto **native Slack
  slashes per command** (cross-platform parity). KiroCrew multiplexes through **one
  `/kirocrew` slash + `!bang` commands**.
- **Trust/approval philosophy.** Hermes = in-band Block Kit tiers (once/session/always/deny),
  agent-thread unblock via `resolve_gateway_approval`. KiroCrew = **layered governance**
  (per-session Trust + global TTL'd policy-gated audited YOLO + late-Trust + dashboard-linked
  durable grants).
- **Multi-user stance.** Hermes is **multi-user by default** (allowlist/allow-all). KiroCrew
  is **owner-only by default and by deliberate lock**, with the multi-user infra present but
  disabled — a policy posture, not a capability gap.
- **Adapter size/shape.** Hermes concentrates Slack logic in one ~6.9k-line `adapter.py`.
  KiroCrew spreads it across many focused modules (`handler`, `events`, `interactions`,
  `approvals`, `channel_lifecycle`, `renderer`, `transport_dispatch`, …).
