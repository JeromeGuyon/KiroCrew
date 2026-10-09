"""Phase 1: slack.accounts config parsing + backward-compat default synthesis."""

from __future__ import annotations

from kiro_crew.config.section_builders import _build_slack_config
from kiro_crew.config.sections import SlackAccountConfig, _parse_slack_accounts


def test_parse_slack_accounts_empty_and_non_dict():
    assert _parse_slack_accounts({}) == {}
    assert _parse_slack_accounts("nope") == {}
    assert _parse_slack_accounts(None) == {}


def test_parse_slack_accounts_valid_entry():
    raw = {
        "acme": {
            "owner_id": "U111",
            "bot_token": "xoxb-1",
            "app_token": "xapp-1",
            "allowed_users": [{"slack_id": "U222", "name": "Bob"}],
            "tracking_channels": [{"channel_id": "C1", "name": "gen"}],
            "open_channels": ["C9"],
            "session_folder": "Acme",
        }
    }
    got = _parse_slack_accounts(raw)
    assert set(got) == {"acme"}
    a = got["acme"]
    assert isinstance(a, SlackAccountConfig)
    assert a.owner_id == "U111"
    assert a.bot_token == "xoxb-1"
    assert a.app_token == "xapp-1"
    assert a.allowed_users == [{"slack_id": "U222", "name": "Bob"}]
    assert a.tracking_channels == [{"channel_id": "C1", "name": "gen"}]
    assert a.open_channels == ["C9"]


def test_parse_slack_accounts_skips_malformed_ids_and_reserves_default():
    bad = {
        "": {},
        "a b": {},
        "bad/id": {},
        "default": {"bot_token": "x"},  # reserved for the implicit top-level account
        "ok_1": {},
        "dash-ok": {},
    }
    got = _parse_slack_accounts(bad)
    assert set(got) == {"ok_1", "dash-ok"}


def test_parse_slack_accounts_token_optional_entry_kept():
    # Token may come from the per-account credential, so an entry without one
    # is still parsed (it just won't start a socket until a credential exists).
    got = _parse_slack_accounts({"ws2": {"owner_id": "U9"}})
    assert set(got) == {"ws2"}
    assert got["ws2"].bot_token == ""
    assert got["ws2"].app_token == ""


def test_parse_slack_accounts_drops_bad_allowlist_and_channel_entries():
    raw = {
        "w": {
            "allowed_users": [{"slack_id": "U1"}, {"name": "no id"}, "garbage"],
            "tracking_channels": [{"channel_id": "C1"}, {"name": "no id"}, 42],
            "open_channels": ["C2", 99, None],
        }
    }
    a = _parse_slack_accounts(raw)["w"]
    assert a.allowed_users == [{"slack_id": "U1"}]
    assert a.tracking_channels == [{"channel_id": "C1"}]
    assert a.open_channels == ["C2"]


def test_build_slack_config_without_accounts_key_is_empty_map():
    cfg = _build_slack_config({"allowed_users": [], "tracking_channels": []})
    assert cfg.accounts == {}


def test_build_slack_config_with_accounts_parsed():
    cfg = _build_slack_config({"accounts": {"acme": {"owner_id": "U111"}}})
    assert set(cfg.accounts) == {"acme"}
    assert cfg.accounts["acme"].owner_id == "U111"
