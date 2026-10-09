"""Phase 3b: per-account authorization isolation (multi-workspace).

The predicates read module state composed onto kiro_crew.slack.handler, so the
tests drive them through that module (same pattern as the existing coverage
tests) and reset the registry + globals around each case.
"""

from __future__ import annotations

import pytest

import kiro_crew.slack.handler as h


@pytest.fixture(autouse=True)
def _reset_authz():
    # Snapshot + restore the globals these tests mutate.
    prev_owner = h._owner_id
    prev_allowed = set(h._allowed_users)
    h.clear_account_authz()
    yield
    h._owner_id = prev_owner
    h._allowed_users = prev_allowed
    h.clear_account_authz()


def test_default_account_unchanged_by_registry():
    h._owner_id = "Uowner"
    h._allowed_users = {"Uowner", "Umember"}
    # default path (no account_id) is byte-for-byte the old behavior
    assert h.is_owner("Uowner") is True
    assert h.is_allowed_user("Umember") is True
    assert h.is_allowed_user("Ustranger") is False


def test_named_account_owner_isolated_from_default():
    h._owner_id = "Udefault"
    h._allowed_users = {"Udefault"}
    h.set_account_authz("acme", owner_id="Uacme", allowed_users=set())
    # Uacme owns 'acme' but NOT the default workspace
    assert h.is_owner("Uacme", "acme") is True
    assert h.is_owner("Uacme") is False
    assert h.is_owner("Uacme", "default") is False
    # Udefault owns default but NOT 'acme'
    assert h.is_owner("Udefault") is True
    assert h.is_owner("Udefault", "acme") is False


def test_named_account_allowlist_does_not_leak_to_other_workspace():
    h._owner_id = "Udefault"
    h._allowed_users = {"Udefault"}
    h.set_account_authz("acme", owner_id="Uacme", allowed_users={"Ualice"})
    h.set_account_authz("globex", owner_id="Uglobex", allowed_users={"Ubob"})
    # Ualice is allowed on acme only
    assert h.is_allowed_user("Ualice", "acme") is True
    assert h.is_allowed_user("Ualice", "globex") is False
    assert h.is_allowed_user("Ualice") is False  # not on default
    # Ubob is allowed on globex only
    assert h.is_allowed_user("Ubob", "globex") is True
    assert h.is_allowed_user("Ubob", "acme") is False


def test_account_owner_always_allowed_even_if_not_in_list():
    h._owner_id = "Udefault"
    h.set_account_authz("acme", owner_id="Uacme", allowed_users=set())
    assert h.is_allowed_user("Uacme", "acme") is True  # owner folded in


def test_unknown_account_authorizes_nobody():
    h._owner_id = "Udefault"
    h._allowed_users = {"Udefault", "Umember"}
    # An account not in the registry is fail-closed, even for the default owner.
    assert h.is_owner("Udefault", "ghost") is False
    assert h.is_allowed_user("Umember", "ghost") is False


def test_set_account_authz_rejects_default():
    h._owner_id = "Udefault"
    h.set_account_authz("default", owner_id="Uhacker", allowed_users={"Uhacker"})
    # 'default' is governed by the globals, not the registry — nothing stored.
    assert "default" not in h._account_authz
    assert h.is_owner("Uhacker") is False
