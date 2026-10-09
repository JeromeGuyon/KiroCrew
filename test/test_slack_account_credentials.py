"""Phase 2: per-account Slack credential names, resolver, and cron scrub."""

from __future__ import annotations

from kiro_crew.config.loader import (
    slack_account_app_token_name,
    slack_account_bot_token_name,
    _SLACK_APP_TOKEN_ACCT_RE,
    _SLACK_BOT_TOKEN_ACCT_RE,
)
from kiro_crew.cron_script import _is_slack_account_token_key


def test_account_token_names_are_hex_suffixed_and_env_safe():
    # 'acme-1' contains a dash (illegal in a POSIX env name) -> hex-encoded.
    bot = slack_account_bot_token_name("acme-1")
    app = slack_account_app_token_name("acme-1")
    assert bot == "SLACK_BOT_TOKEN__" + "acme-1".encode().hex().upper()
    assert app == "SLACK_APP_TOKEN__" + "acme-1".encode().hex().upper()
    assert bot.replace("SLACK_BOT_TOKEN__", "").isalnum()
    assert _SLACK_BOT_TOKEN_ACCT_RE.match(bot)
    assert _SLACK_APP_TOKEN_ACCT_RE.match(app)


def test_account_token_regex_rejects_base_and_foreign_keys():
    assert not _SLACK_BOT_TOKEN_ACCT_RE.match("SLACK_BOT_TOKEN")  # base, no suffix
    assert not _SLACK_BOT_TOKEN_ACCT_RE.match("SLACK_BOT_TOKEN__XYZ")  # non-hex
    assert not _SLACK_APP_TOKEN_ACCT_RE.match("JIRA_TOKEN_41")


def test_cron_scrub_recognizes_per_account_tokens():
    assert _is_slack_account_token_key("SLACK_BOT_TOKEN__41434D45")
    assert _is_slack_account_token_key("SLACK_APP_TOKEN__41434D45")
    assert not _is_slack_account_token_key("SLACK_BOT_TOKEN")
    assert not _is_slack_account_token_key("UNRELATED")


def _make_cfg(accounts: dict | None = None):
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.config.section_builders import _build_slack_config

    cfg = KiroCrewConfig()
    cfg.slack = _build_slack_config({"accounts": accounts or {}})
    return cfg


def test_resolve_default_account_uses_credentials_only():
    cfg = _make_cfg()
    creds = {
        "SLACK_BOT_TOKEN": "xoxb-top",
        "SLACK_APP_TOKEN": "xapp-top",
        "KIROCREW_OWNER_ID": "Uowner",
    }
    got = cfg.resolve_slack_account("default", creds)
    assert got == {"bot_token": "xoxb-top", "app_token": "xapp-top", "owner_id": "Uowner"}


def test_resolve_named_account_credential_first_then_config_fallback():
    cfg = _make_cfg(
        {"acme": {"owner_id": "Uacme", "bot_token": "xoxb-cfg", "app_token": "xapp-cfg"}}
    )
    bot_key = slack_account_bot_token_name("acme")
    creds = {bot_key: "xoxb-cred", "KIROCREW_OWNER_ID": "Uglobal"}
    got = cfg.resolve_slack_account("acme", creds)
    assert got["bot_token"] == "xoxb-cred"  # credential wins
    assert got["app_token"] == "xapp-cfg"  # no credential -> config fallback
    assert got["owner_id"] == "Uacme"  # account owner wins over global


def test_resolve_named_account_owner_falls_back_to_global():
    cfg = _make_cfg({"ws": {"bot_token": "b", "app_token": "a"}})  # no owner_id
    got = cfg.resolve_slack_account("ws", {"KIROCREW_OWNER_ID": "Uglobal"})
    assert got["owner_id"] == "Uglobal"


def test_resolve_unknown_account_is_empty():
    cfg = _make_cfg()
    assert cfg.resolve_slack_account("nope", {}) == {
        "bot_token": "",
        "app_token": "",
        "owner_id": "",
    }
