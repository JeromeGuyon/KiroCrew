"""``session/spawned``: the creating edge recorded on the CREATOR's log at mint (#18341).

A ``session_create`` child writes its own ``session/opened.parent`` only at its first
turn, because its log is keyed by an ACP session id that does not exist at mint. Until
then the edge lived in gateway memory alone, so a restart in between lost it and the
child floated at the sidebar's top level. The creator's log does exist at mint, so the
edge is written there, and the tree folds it when the child's own records name no
parent.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kiro_crew import crew_log as lg
from kiro_crew.crew_log import CrewLog, emit
from kiro_crew.crew_log import session_tree_projection as stp
from kiro_crew.crew_log import store as crew_store
from kiro_crew.crew_log.session_tree import (
    EdgeRecord,
    OpenedRecord,
    SessionTree,
    fold_tree,
    spawned_record,
)
from kiro_crew.crew_log.session_tree_projection import CHECKPOINT_NAME, SessionTreeProjection
from kiro_crew.dashboard.session_memory import lineage_parents

GATEWAY = "gateway"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Own data home per test, and no checkpoint write armed on the real pool (same
    fixture shape as the adoption suite, for the same reasons)."""
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(emit.CREW_LOG_ENV, "1")
    monkeypatch.setattr(
        "kiro_crew.executors.maintenance_executor",
        lambda: type("_NoPool", (), {"submit": staticmethod(lambda *a, **k: None)}),
    )
    stp.reset_for_tests()
    yield
    stp.reset_for_tests()
    emit.reset_caches()


def _rec(sid: str, slot: str, created: int = 1, parent: str | None = None) -> OpenedRecord:
    return OpenedRecord(sid=sid, slot=slot, created_at=created, parent_slot=parent)


def _spawn(child: str, creator: str, sid: str, at: int = 10, seq: int = 2) -> EdgeRecord:
    return EdgeRecord(slot=child, parent_slot=creator, at=at, sid=sid, seq=seq)


def _open_creator(sid: str, slot: str) -> None:
    emit.on_session_opened(sid, agent="kirocrew", slot=slot, model="opus", cwd="/w", owner="o")


def _restart() -> SessionTreeProjection:
    """What a gateway restart leaves: no in-memory state, only the disk."""
    stp.reset_for_tests()
    emit.reset_caches()
    revived = SessionTreeProjection()
    revived.ensure_seeded()
    return revived


def _checkpoint_path():
    return crew_store.crew_log_root(lg.KIND_SESSION) / "projections" / CHECKPOINT_NAME


# ── the fold ───────────────────────────────────────────────────────────────


def test_a_spawn_gives_a_child_with_no_log_a_node_under_its_creator():
    nodes = fold_tree([_rec("s-l", "L")], spawned=[_spawn("W", "L", "s-l")])
    assert nodes["W"].parent_slot == "L"
    assert nodes["W"].cycle is False


def test_the_childs_own_opened_parent_wins_over_a_spawn():
    records = [_rec("s-l", "L"), _rec("s-m", "M"), _rec("s-w", "W", created=2, parent="M")]
    nodes = fold_tree(records, spawned=[_spawn("W", "L", "s-l")])
    assert nodes["W"].parent_slot == "M"


def test_a_spawn_fills_a_child_whose_own_log_names_no_parent():
    """The child's first turn ran after the restart, so its log carries no parent."""
    records = [_rec("s-l", "L"), _rec("s-w", "W", created=2)]
    nodes = fold_tree(records, spawned=[_spawn("W", "L", "s-l")])
    assert nodes["W"].parent_slot == "L"


def test_adoption_and_release_still_win_over_a_spawn():
    records = [_rec("s-l", "L"), _rec("s-d", "D"), _rec("s-w", "W", created=2)]
    spawned = [_spawn("W", "L", "s-l")]
    adopted = fold_tree(records, [EdgeRecord("W", "D", at=50, sid="s-w", seq=3)], spawned)
    assert adopted["W"].parent_slot == "D"
    released = fold_tree(records, [EdgeRecord("W", None, at=50, sid="s-w", seq=3)], spawned)
    assert released["W"].parent_slot is None


def test_a_spawn_whose_creator_has_no_log_is_not_folded():
    nodes = fold_tree([_rec("s-x", "X")], spawned=[_spawn("W", "L", "s-l")])
    assert "W" not in nodes


def test_the_creator_comes_from_the_unit_never_from_the_entry():
    entry = lg.Entry.from_dict(
        {
            "seq": 4,
            "time": 9,
            "type": "session/spawned",
            "src": GATEWAY,
            "data": {"child": {"slot": "W"}, "parent": {"slot": "FORGED"}},
        }
    )
    spawn = spawned_record("L", "s-l", entry)
    assert spawn == EdgeRecord(slot="W", parent_slot="L", at=9, sid="s-l", seq=4)
    assert spawned_record("W", "s-w", entry) is None  # a child naming itself


# ── the emitter and the restart ────────────────────────────────────────────


def test_mint_then_restart_with_no_child_first_turn_still_nests_the_child():
    """The #18341 case: mint, restart before the child's first turn, cold seed."""
    _open_creator("s-l", "L")
    emit.on_session_spawned("s-l", creator_slot="L", child_slot="W")
    assert emit.flush(timeout=5.0) is True
    # In process the fold already has it, from the emitter's own apply.
    assert stp.projection().nodes()["W"].parent_slot == "L"

    directory = crew_store.unit_dir_for(lg.KIND_SESSION, "s-l")
    assert crew_store.find_spawned_entries(directory, 10)[0].data == {"child": {"slot": "W"}}
    # No unit was ever written for the child.
    assert crew_store.unit_dir_for(lg.KIND_SESSION, "s-w") is None or not (
        crew_store.unit_dir_for(lg.KIND_SESSION, "s-w").exists()
    )

    revived = _restart()
    assert revived.nodes()["W"].parent_slot == "L"

    # The join the sidebar reads: the restored child row has NO mint witness, yet it
    # still nests under the live creator row, from the folded node.
    rows = [
        {"key": "L"},
        {"key": "W", "lineage_minted": False, "created_by": "L"},
    ]
    parents = lineage_parents(rows, revived.nodes())
    assert parents["W"] == {"slot": "L", "key": "L"}


def test_the_spawn_survives_a_restart_through_the_checkpoint_too():
    _open_creator("s-l", "L")
    emit.on_session_spawned("s-l", creator_slot="L", child_slot="W")
    assert emit.flush(timeout=5.0) is True
    first = _restart()
    assert first.flush_checkpoint() is True
    payload = json.loads(_checkpoint_path().read_text(encoding="utf-8"))
    assert payload["spawned"] and payload["spawned"][0]["slot"] == "W"

    revived = _restart()
    assert revived.nodes()["W"].parent_slot == "L"


def test_a_cold_scan_reads_the_spawn_like_the_projection():
    _open_creator("s-l", "L")
    emit.on_session_spawned("s-l", creator_slot="L", child_slot="W")
    assert emit.flush(timeout=5.0) is True
    reading = SessionTree().reading(with_edges=True)
    assert reading.nodes["W"].parent_slot == "L"
    assert not reading.incomplete


def test_removing_the_creator_unit_drops_its_spawns():
    _open_creator("s-l", "L")
    emit.on_session_spawned("s-l", creator_slot="L", child_slot="W")
    assert emit.flush(timeout=5.0) is True
    proj = stp.projection()
    assert "W" in proj.nodes()
    proj.forget("s-l")
    assert "W" not in proj.nodes()


def test_a_creator_with_no_log_writes_nothing():
    """No ACP session for the creator: today's in-memory behavior, nothing on disk."""
    emit.on_session_spawned("s-none", creator_slot="L", child_slot="W")
    assert emit.flush(timeout=5.0) is True
    assert "W" not in stp.projection().nodes()


# ── session_control's call ─────────────────────────────────────────────────


def test_session_control_records_only_a_witnessed_mint(monkeypatch):
    from kiro_crew.dashboard import session_control as sc

    calls: list[tuple] = []
    monkeypatch.setattr(
        sc.crew_log_emit,
        "on_session_spawned",
        lambda sid, **kw: calls.append((sid, kw)),
    )
    minted = SimpleNamespace(key="W", _lineage_minted=True, _created_by="L", _created_by_sid="s-l")
    sc._record_spawn("L", minted)
    assert calls == [("s-l", {"creator_slot": "L", "child_slot": "W"})]

    calls.clear()
    # Restored (no witness), no creator session, or a caller that is not the minter.
    sc._record_spawn("L", SimpleNamespace(**{**vars(minted), "_lineage_minted": False}))
    sc._record_spawn("L", SimpleNamespace(**{**vars(minted), "_created_by_sid": ""}))
    sc._record_spawn("OTHER", minted)
    assert calls == []


def test_the_creator_log_is_a_real_unit_with_the_entry_type_registered():
    handle = CrewLog.create(lg.KIND_SESSION, "s-l", owner="o", agent="kirocrew", slot="L")
    handle.append(
        "session/opened",
        {
            "agent": "kirocrew",
            "slot": "L",
            "model": "opus",
            "cwd": "/w",
            "owner": "o",
            "resumed": False,
        },
        src=GATEWAY,
    )
    written = handle.append("session/spawned", {"child": {"slot": "W"}}, src=GATEWAY)
    assert written is not None and written.type == "session/spawned"
