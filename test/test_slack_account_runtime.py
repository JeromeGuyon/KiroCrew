"""Phase 3a: per-account Slack runtime map built at gateway construction."""

from __future__ import annotations

from kiro_crew.config.loader import (
    KiroCrewConfig,
    slack_account_bot_token_name,
)
from kiro_crew.config.section_builders import _build_slack_config
from kiro_crew.slack.gateway import GatewayOrchestrator, SlackAccountRuntime


def _cfg(accounts: dict | None = None) -> KiroCrewConfig:
    cfg = KiroCrewConfig()
    cfg.slack = _build_slack_config({"accounts": accounts or {}})
    return cfg


def _build(cfg: KiroCrewConfig, creds: dict) -> dict[str, SlackAccountRuntime]:
    # Call the pure builder unbound — it only touches cfg + creds, no self state.
    return GatewayOrchestrator._build_slack_runtime(
        object.__new__(GatewayOrchestrator), cfg, creds
    )


def test_runtime_always_has_default_entry():
    rt = _build(_cfg(), {})
    assert set(rt) == {"default"}
    assert rt["default"].account_id == "default"


def test_default_entry_mirrors_top_level_credentials():
    rt = _build(
        _cfg(),
        {"SLACK_BOT_TOKEN": "xoxb-top", "SLACK_APP_TOKEN": "xapp-top", "KIROCREW_OWNER_ID": "Uo"},
    )
    d = rt["default"]
    assert d.bot_token == "xoxb-top"
    assert d.app_token == "xapp-top"
    assert d.owner_id == "Uo"
    assert d.enabled is True


def test_named_account_resolved_credential_first():
    cfg = _cfg({"acme": {"owner_id": "Ua", "bot_token": "cfgbot", "app_token": "cfgapp"}})
    creds = {slack_account_bot_token_name("acme"): "credbot", "KIROCREW_OWNER_ID": "Uglobal"}
    rt = _build(cfg, creds)
    assert set(rt) == {"default", "acme"}
    a = rt["acme"]
    assert a.bot_token == "credbot"  # credential wins
    assert a.app_token == "cfgapp"  # config fallback
    assert a.owner_id == "Ua"  # account owner


def test_half_configured_account_carried_but_disabled():
    # app_token missing -> entry exists (round-trip/diagnostics) but not enabled.
    cfg = _cfg({"ws": {"bot_token": "b"}})
    rt = _build(cfg, {})
    assert "ws" in rt
    assert rt["ws"].enabled is False


def test_team_id_empty_until_connect():
    rt = _build(_cfg({"ws": {"bot_token": "b", "app_token": "a"}}), {})
    assert rt["ws"].team_id == ""
