# Connecting multiple Slack workspaces

One Kiro Crew gateway can serve **several independent Slack workspaces** at once.
Each workspace has its own bot, its own owner, and its own allow-list, and they
are fully isolated: a user authorized on one workspace has no authority over
another, and a reply always goes out through the bot of the workspace the
message came from.

> **Scope.** This is for *independent* workspaces (separate Slack installs). It
> is **not** Enterprise Grid: there is no shared org, and routing is 1:1 between
> a workspace and an account.

## Concepts

- **The `default` account** is your existing single-workspace setup — the
  top-level `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` and `KIROCREW_OWNER_ID`.
  Nothing about it changes when you add more workspaces.
- **A named account** is an additional workspace, identified by a short
  `account_id` you choose (letters, digits, `-`, `_`; `default` is reserved).
  It lives under `slack.accounts.<account_id>` in config and carries its own
  owner + allow-list; its tokens come from per-account credentials.

## Add a second workspace

### 1. Create a Slack app for the new workspace

Install a Slack app in the second workspace exactly as you did for the first
(Socket Mode enabled). You end up with that workspace's **bot token**
(`xoxb-…`) and **app-level token** (`xapp-…`).

### 2. Give the account its owner + allow-list

**Dashboard → Settings → Slack → Additional workspaces → Add workspace.**
Type an `account_id` (e.g. `acme`), then set:

- **Owner ID** — the Slack member ID that owns *this* workspace.
- **Allowed users** — member IDs (besides the owner) allowed on this workspace.

Save. (Tokens are **not** entered here — see the next step.) The badges show
`bot —` / `app —` until the tokens are provisioned.

### 3. Provision the account's tokens as credentials

Tokens are never stored in the config file. They are credentials named with a
**hex-encoded** account id so the env-var name is always valid:

```
SLACK_BOT_TOKEN__<HEX>   = xoxb-…   (this workspace's bot token)
SLACK_APP_TOKEN__<HEX>   = xapp-…   (this workspace's app token)
```

where `<HEX>` is the account id encoded as uppercase hex. For `acme`:

```python
>>> "acme".encode().hex().upper()
'41434D45'
# -> SLACK_BOT_TOKEN__41434D45, SLACK_APP_TOKEN__41434D45
```

Add both lines to `~/.kiro/crew/.env` (or your credential store).

### 4. Restart the gateway

Adding a workspace is boot-read, so restart to mount its socket:

```
kirocrew restart
```

On boot you should see `slack: mounted socket for workspace account 'acme'` and
`slack: connected workspace account 'acme'` in the logs.

## How it behaves

- **Inbound.** A message arrives on the socket of the workspace it was sent in.
  Authorization is checked against *that* workspace's owner + allow-list only.
  An unknown workspace is rejected (fail-closed).
- **Outbound.** The reply is posted with *that* workspace's bot token — the
  default bot never answers another workspace's message.
- **Sessions.** Keyed by Slack channel/DM/thread IDs, which are globally unique,
  so sessions never collide across workspaces.
- **Isolation.** Owner A cannot drive the machine via workspace B; A's
  allow-list grants nothing on B.

## Troubleshooting

- **Badge stays `bot —` / `app —` after restart.** The per-account credential is
  missing or misnamed. Re-check the hex of the account id and that both
  `SLACK_BOT_TOKEN__<HEX>` and `SLACK_APP_TOKEN__<HEX>` are set.
- **Workspace never connects** (`failed to connect` in logs). That workspace's
  app token is wrong or Socket Mode is off; the other workspaces keep working
  (a per-account connect failure is isolated).
- **A member is ignored on the new workspace.** They are not on *that*
  workspace's allow-list — add their member ID under the account, not the
  top-level one.
- **Nothing changed for my existing setup.** Correct — with no
  `slack.accounts`, the gateway behaves exactly as before (the `default`
  account is your single workspace).

## Removing a workspace

Remove it in **Settings → Slack → Additional workspaces** (the ✕ on its row) and
delete its `SLACK_BOT_TOKEN__<HEX>` / `SLACK_APP_TOKEN__<HEX>` credentials, then
restart. Its config entry (and any member display names) is preserved until you
remove it, so an owner/allow-list edit never silently drops it.
