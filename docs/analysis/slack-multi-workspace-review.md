# Multi-workspace Slack — implementation review

**Date:** 2026-10-09
**Branch:** `feature/slack-multi-workspace` (15 commits, +1494/−17, 21 files)
**Reviewer:** self-review before e2e

## 1. Subject recap

Let one Kiro Crew gateway serve N **independent** Slack workspaces (not
Enterprise Grid), each with its own bot, owner and allow-list, fully isolated
inbound and outbound. The existing single-workspace install is the implicit
`default` account and must behave exactly as before.

## 2. Architecture, end to end

| Layer | What it does | Where |
|---|---|---|
| Config | `slack.accounts` map of `SlackAccountConfig`; `_parse_slack_accounts` (active, fail-safe, reserves `default`) | `config/sections.py`, `section_builders.py` |
| Credentials | hex-suffixed `SLACK_BOT_TOKEN__<HEX>` / `SLACK_APP_TOKEN__<HEX>`; recognised (no warn, scrub-skip); `resolve_slack_account()` credential-first | `config/loader.py` |
| Runtime | `SlackAccountRuntime` map built at boot (no I/O); `.enabled` = both tokens | `slack/gateway.py` |
| Authz | `_account_authz` registry; `is_owner/is_allowed_user(uid, account_id)` — named account reads only its own, unknown = fail-closed | `slack/handler_runtime/access.py`, `handler.py` |
| Sockets | one `WSSocketModeClient` per enabled account; shared `_on_event`; `client id -> account_id` map | `slack/events.py` |
| Inbound routing | `account_id` from the delivering socket → `_route_message` gate `is_allowed_user(sender, account_id)` | `slack/events.py` |
| Outbound routing | `orch.slack_for(account_id)` → `handle_message` chokepoint; carried through the queue | `slack/events.py`, `gateway.py` |
| Lifecycle | per-account connect (isolated failure) + shutdown close | `gateway_runtime/channel_lifecycle.py`, `gateway.py` |
| Cron scrub | per-account tokens dropped by prefix (exact-membership `_CRON_ENV_DENY` would miss the suffix) | `cron_script.py` |
| UI | Settings → Slack → Additional workspaces editor (owner + allow-list; read-only token badges) | `website/.../SlackPanel.tsx`, `messaging.ts` |
| Docs | design + audit + user guide | `docs/` |

## 3. Security review

- **Inbound isolation ✓** — the gate reads the delivering workspace's own
  owner+allow-list; an unknown account authorizes nobody (fail-closed). Owner A
  is not owner B; A's allow-list grants nothing on B. Proven by
  `test_slack_account_authz.py` (6 cross-workspace cases).
- **Outbound isolation ✓** — the reply client is the delivering workspace's own
  (`slack_for(account_id)`), so B's message is answered by B's bot, never the
  default. Live path + queued/transport paths all routed.
- **Credential hygiene ✓** — per-account tokens are hex-named, regex-restricted
  to hex (a forged multiline key cannot reach the credential path), scrubbed
  from agent subprocess env (sandbox prefix) AND from cron env (prefix check
  added — the one gap exact-membership would have missed).
- **No socket without an owner ✓** — a named account whose authz was not
  registered is skipped at mount (defense in depth).
- **`default` reserved ✓** — rejected in the config parser, the authz setter,
  and the dashboard save, so there is never a second source of truth for it.

## 4. Backward compatibility

- No `slack.accounts` → empty runtime map (minus the always-present `default`
  entry), empty mount loop, empty authz registry. Every `is_owner(uid)` /
  `is_allowed_user(uid)` call site unchanged (default param). The socket, the
  reply client, the session keys are byte-identical to before. 502 pre-existing
  Slack tests stay green.

## 5. Findings

### F1 — Enterprise origin gate is still single-workspace (LOW, documented)

`check_message_origin(team_id)` validates against `_validated_team_id` /
`_allowed_team_ids`, filled once at boot from the **default** account's
`auth.test`. For a named workspace:

- **Default case (no `slack.allowed_enterprise_ids`): no impact** — the gate is
  **default-open** and returns True for any team_id, so named-workspace messages
  pass.
- **If the operator configured an enterprise allowlist:** they must add each
  named workspace's `T…` id to `slack.allowed_enterprise_ids`, or its messages
  are rejected. The existing `_diagnose_allowlist_id_spaces` load-time warning
  surfaces a mis-set allowlist.

Not a blocker for the independent-workspace target. A full fix would validate
each account's own `team_id` at its connect and gate per account — deferred;
noted here and in the user doc's troubleshooting.

### F2 — `_maybe_prompt_owner` is a no-op (by design)

Channel-join Allow/Deny prompts are disabled in this build, so there is no
per-account owner-prompt to add. If re-enabled, pass the socket's `account_id`.

### F3 — Session key not account-scoped (by design, safe)

Slack channel/DM/thread IDs are globally unique, so no cross-workspace
collision is possible; adding `account_id` would add risk (every
stop/dequeue/dashboard site must match) for no benefit.

## 6. Test coverage

- Backend: config parse (7), credentials+resolver (8), runtime map (6), authz
  isolation (6), socket mounting (3), settings GET/PUT accounts (4) + the 502
  pre-existing Slack tests still green.
- Frontend: SlackPanel accounts editor render + add-to-payload (2), tsc clean,
  36 SlackPanel coverage tests green.

## 7. Verdict

Backend + UI + docs complete and internally consistent; `default` path provably
unchanged; isolation holds inbound and outbound. One documented low-severity
gap (F1, enterprise allowlist + named workspaces) that does not affect the
default/independent-workspace case. **Ready for end-to-end validation on a QA
pod with a real second token** — the one thing unit tests cannot prove (a real
socket mounting and a real reply going out on the right bot).
