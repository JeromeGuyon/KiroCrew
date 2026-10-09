"""Phase 3c-ii: per-account Socket Mode client mounting (mocked, no network)."""

from __future__ import annotations

import types

import pytest

import kiro_crew.slack.events as events
from kiro_crew.slack.gateway import SlackAccountRuntime


class _FakeSocket:
    def __init__(self, app_token=None, web_client=None):
        self.app_token = app_token
        self.web_client = web_client
        self.socket_mode_request_listeners = []


class _FakeWebClient:
    def __init__(self, token=None):
        self.token = token


class _Orch:
    """Minimal orchestrator stub exercising init_socket_mode's mounting half."""

    def __init__(self, runtime):
        self._slack_enabled = True
        self._owner_id = "Uowner"
        self._bot_token = "xoxb-default"
        self._app_token = "xapp-default"
        self._allowed_users = {"Uowner"}
        self._tracking_channels = set()
        self._open_channels = set()
        self._slack_runtime = runtime
        self.sessions = None
        self.dashboard_state = None
        self._handler_tasks = set()
        self._socket_client = None
        self._slack_account_sockets = {}
        self._slack_socket_account = {}
        # cfg stub: accounts + enterprise ids + yolo flag
        self._cfg = types.SimpleNamespace(
            slack=types.SimpleNamespace(accounts={}, trusted_bot_ids=set()),
            slack_enterprise_ids=[],
            agent=types.SimpleNamespace(dangerously_skip_permissions=False),
        )


@pytest.fixture
def _patch_slack(monkeypatch):
    monkeypatch.setattr(events, "WSSocketModeClient", _FakeSocket)
    monkeypatch.setattr(events, "AsyncWebClient", _FakeWebClient)
    # Neutralize the enterprise auth.test gate (it does a network call).
    import kiro_crew.slack.enterprise as ent  # noqa: F401

    monkeypatch.setattr(
        events, "current_context", lambda: types.SimpleNamespace(
            slack_gate=types.SimpleNamespace(validate_enterprise=lambda *a, **k: True)
        )
    )
    # The authz/dashboard setters are no-ops for this structural test.
    for name in (
        "set_allowed_users", "set_tracking_channels", "set_open_channels",
        "set_owner_id", "set_orch_cfg", "set_dashboard_state",
        "set_account_authz", "clear_account_authz",
    ):
        monkeypatch.setattr(events, name, lambda *a, **k: None, raising=False)


async def _run(orch):
    try:
        seen = events.SeenCache()
    except Exception:
        seen = object()
    await events.init_socket_mode(orch, seen)


@pytest.mark.asyncio
async def test_only_default_socket_when_no_accounts(_patch_slack):
    rt = {"default": SlackAccountRuntime("default", "xoxb-default", "xapp-default", "Uowner")}
    orch = _Orch(rt)
    await _run(orch)
    assert orch._socket_client is not None
    assert orch._slack_account_sockets == {}
    assert orch._slack_socket_account[id(orch._socket_client)] == "default"


@pytest.mark.asyncio
async def test_enabled_named_account_gets_its_own_socket(_patch_slack):
    rt = {
        "default": SlackAccountRuntime("default", "xoxb-default", "xapp-default", "Uowner"),
        "acme": SlackAccountRuntime("acme", "xoxb-acme", "xapp-acme", "Uacme"),
    }
    orch = _Orch(rt)
    await _run(orch)
    assert set(orch._slack_account_sockets) == {"acme"}
    acme_sock = orch._slack_account_sockets["acme"]
    # routing map attributes the acme socket to the acme workspace
    assert orch._slack_socket_account[id(acme_sock)] == "acme"
    # the acme socket carries the acme app token + bot web client
    assert acme_sock.app_token == "xapp-acme"
    assert acme_sock.web_client.token == "xoxb-acme"


@pytest.mark.asyncio
async def test_half_configured_named_account_is_not_mounted(_patch_slack):
    rt = {
        "default": SlackAccountRuntime("default", "xoxb-default", "xapp-default", "Uowner"),
        "ws": SlackAccountRuntime("ws", "xoxb-ws", "", "Uws"),  # no app token -> disabled
    }
    orch = _Orch(rt)
    await _run(orch)
    assert "ws" not in orch._slack_account_sockets
