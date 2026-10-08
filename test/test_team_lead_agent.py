"""The team-lead crewmate: its spec, its charter, its provisioning and its skill.

Follows ``test_security_conductor_agent.py``'s installer half -- stub the agents
dir and ``build_agent_config``, run the installer, assert on the JSON it wrote --
and adds the three halves that agent does not have: a charter whose contract
sentences are pinned because the capability is the sentence, a crewmate record
whose provisioning must be safe to repeat on every boot, and a skill that reuses
another skill's scripts rather than carrying a copy.

Each class is named so it can be selected alone with ``-k``.
"""

from __future__ import annotations

import json
import pathlib

from kiro_crew import agent, agent_state, subagent
from kiro_crew.agent_files import (
    OWNED_KIRO_AGENT_FILES,
    REQUIRED_KIRO_AGENT_FILES,
    TEAM_LEAD_AGENT_FILENAME,
)
from kiro_crew.kiro_cli import SPEC_PERMISSIONS_MIN_VERSION

_ACCEPTS = SPEC_PERMISSIONS_MIN_VERSION
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SKILLS = _REPO_ROOT / "src" / "kiro_crew" / "builtin_skills"

#: The keys a DECLINE is permitted to change on somebody else's file, and the only ones.
#: Every other key must come back identical, which is what :func:`_without_gate_skips`
#: lets each declined-path test assert in one line instead of naming fields it cares
#: about -- a decline that quietly dropped the operator's ``resources`` or ``model``
#: would pass a field-by-field check that nobody remembered to extend.
_STRIPPED_KEYS = frozenset({"allowedTools", "includeMcpJson", "useLegacyMcpJson", "permissions"})


def _without_gate_skips(spec: dict) -> dict:
    """*spec* with every channel a decline may strip removed, servers included.

    ``autoApprove`` is nested inside each ``mcpServers`` entry rather than being a
    top-level key, so it needs its own removal: comparing the server maps whole would
    report a stripped grant as the operator losing a server.
    """
    out = {k: v for k, v in spec.items() if k not in _STRIPPED_KEYS}
    servers = out.get("mcpServers")
    if isinstance(servers, dict):
        out["mcpServers"] = {
            n: ({k: v for k, v in e.items() if k != "autoApprove"} if isinstance(e, dict) else e)
            for n, e in servers.items()
        }
    return out


def _stub_environment(tmp_path, monkeypatch, *, may_auto_approve=None) -> None:
    """Pin the agents dir, the template and the writer's version gate.

    The template mirrors what ``config/defaults.json`` ships in the one respect
    these tests turn on: ``fs_write`` mounted and ungranted.
    """
    monkeypatch.setattr(agent, "kiro_agents_dir_path", lambda: tmp_path)
    monkeypatch.setattr("kiro_crew.kiro_cli.installed_kiro_cli_version", lambda: _ACCEPTS)
    monkeypatch.setattr(
        agent,
        "build_agent_config",
        lambda: {
            "name": "kirocrew",
            "prompt": "file://x",
            "mcpServers": {
                "kirocrew-core": {"command": "/resolved/kirocrew", "args": ["mcp-core"]},
                "builder-mcp": {"command": "/x/builder", "args": []},
            },
            "tools": ["fs_write", "code", "execute_bash", "fs_read", "@kirocrew-core"],
            "allowedTools": ["fs_read", "@kirocrew-core"],
        },
    )
    monkeypatch.setattr(
        agent, "_kirocrew_mcp_invocation", lambda sub: ("/resolved/kirocrew", [sub])
    )
    monkeypatch.setattr(agent, "_may_auto_approve", may_auto_approve or (lambda ref: True))


def _install(tmp_path, monkeypatch, *, may_auto_approve=None) -> dict:
    _stub_environment(tmp_path, monkeypatch, may_auto_approve=may_auto_approve)
    assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
    return json.loads((tmp_path / TEAM_LEAD_AGENT_FILENAME).read_text(encoding="utf-8"))


def _charter(tmp_path, monkeypatch) -> str:
    """The emitted prompt with its whitespace normalized, so a reflow cannot
    break an assertion about a sentence the charter actually makes."""
    return " ".join(_install(tmp_path, monkeypatch)["prompt"].split())


class TestTeamLeadSpecIsMaterializedByProductCode:
    def test_the_installer_writes_the_spec(self, tmp_path, monkeypatch):
        """The whole point of a shipped crewmate: the file is produced by an
        installer the rebuild calls, never by a hand that drops it in."""
        data = _install(tmp_path, monkeypatch)
        assert data["name"] == "kirocrew-team-lead"
        assert (tmp_path / TEAM_LEAD_AGENT_FILENAME).is_file()

    def test_the_rebuild_calls_it(self):
        """Eager, beside the other generated specs. ``session_create`` resolves an
        agent from a boot-time in-memory snapshot that no spec write refreshes, so
        a spec materialized later cannot be dispatched by name at all."""
        source = (_REPO_ROOT / "src" / "kiro_crew" / "agent.py").read_text(encoding="utf-8")
        assert "team_lead_agent._install_team_lead_agent(clean=clean)" in source

    def test_only_a_transient_hold_enters_the_retry_set(self):
        """``_conductor_spec_held`` feeds an hourly retry sweep, so a PERMANENT
        decline must not be counted there: the file is still somebody else's on
        every later pass, which makes it a retry that can never succeed and a log
        line reading as a failure for a correct decision."""
        source = (_REPO_ROOT / "src" / "kiro_crew" / "agent.py").read_text(encoding="utf-8")
        head, _, rest = source.partition("team_lead_agent._install_team_lead_agent(clean=clean)")
        assert rest, "the rebuild no longer calls the installer the way this test reads"
        assert "InstallOutcome.HELD" in rest[:200]
        # Not the collapsed form, which would count DECLINED as held.
        assert "not team_lead_agent._install_team_lead_agent" not in source

    def test_filename_is_owned_but_not_required(self):
        """Owned, so the convergence sweep rewrites it when the Playwright servers
        move. NOT required: that list fails every turn when its file is absent,
        and this spec's absence disables one feature."""
        assert TEAM_LEAD_AGENT_FILENAME in OWNED_KIRO_AGENT_FILES
        assert TEAM_LEAD_AGENT_FILENAME not in REQUIRED_KIRO_AGENT_FILES

    def test_the_agent_is_advertised(self):
        """The opposite of every conductor, and for the reason that set exists: a
        conductor is withheld from the roster because work handed to it cannot be
        done, having no file-writing tool. This one can do the work."""
        assert "kirocrew-team-lead" not in subagent.UNADVERTISED_AGENTS


class TestTeamLeadInstallerDeclinesAForeignSpec:
    """Provenance, not existence. The spec id makes a collision unlikely rather
    than impossible, and an operator's own agent at this filename is not an
    out-of-date spec: replacing it destroys whatever put it there."""

    def test_a_crew_of_its_own_at_this_filename_is_left_alone(self, tmp_path, monkeypatch):
        """Left alone in everything but auto-approval. Their name, prompt and tool list
        are theirs; what a decline takes is the gate-skipping channels, and on a file
        carrying none of them the only one left to close is the ambient MCP merge --
        which is open when ``includeMcpJson`` is true OR ABSENT."""
        foreign = {
            "name": "my-own-lead",
            "prompt": "mine",
            "tools": ["fs_write"],
            "mcpServers": {},
        }
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        target.write_text(json.dumps(foreign), encoding="utf-8")
        _stub_environment(tmp_path, monkeypatch)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        after = json.loads(target.read_text(encoding="utf-8"))
        assert _without_gate_skips(after) == _without_gate_skips(foreign)
        # `.get`, so a missing key fails this as an assertion rather than a KeyError: the
        # absent-key case is precisely the one that is ambient-ON, so it must be the thing
        # under test and not an incidental traceback.
        assert after.get("includeMcpJson") is False, (
            "a file omitting the key was left omitting it, and absent means the ambient "
            "merge is ON -- so an inherited auto-approved server still reaches this agent"
        )
        assert after["tools"] == ["fs_write"], "mounting is not auto-approving"

    def test_declining_is_reported_at_error_level(self, tmp_path, monkeypatch):
        """The installer's own caller swallows at debug, so without this the one
        event an operator needs is invisible at any ordinary level."""
        (tmp_path / TEAM_LEAD_AGENT_FILENAME).write_text(
            json.dumps({"name": "my-own-lead"}), encoding="utf-8"
        )
        _stub_environment(tmp_path, monkeypatch)
        errors: list[str] = []
        monkeypatch.setattr(
            agent.logger, "error", lambda msg, *a, **kw: errors.append(msg % a if a else msg)
        )
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        assert errors and "my-own-lead" in errors[0]

    def test_its_own_previous_output_is_rewritten(self, tmp_path, monkeypatch):
        """Idempotence depends on this: the marks are what the installer itself
        writes, so a second rebuild re-derives rather than declining forever."""
        _stub_environment(tmp_path, monkeypatch)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN

    def test_a_missing_defining_server_is_named_in_the_reason(self):
        """A spec sharing the name but not the mounts is somebody else's. The
        reason says which mark failed, so the log tells an operator what to look
        at rather than only that something is in the way."""
        for absent in agent._DEFINING_SERVERS:
            present = [s for s in agent._DEFINING_SERVERS if s != absent]
            spec = {
                "name": "kirocrew-team-lead",
                "mcpServers": {s: {} for s in present},
            }
            reason = agent._foreign_team_lead_spec_reason(spec)
            assert reason is not None and absent in reason, absent

    def test_only_the_shape_this_release_writes_is_accepted(self):
        """Nothing is healed, because there is no earlier release of this spec to
        heal. The mount must be in ``mcpServers``; the same reference reached
        through ``tools`` or a per-tool grant is somebody else's file."""
        assert (
            agent._foreign_team_lead_spec_reason(
                {
                    "name": "kirocrew-team-lead",
                    "mcpServers": {s: {} for s in agent._DEFINING_SERVERS},
                }
            )
            is None
        )
        reason = agent._foreign_team_lead_spec_reason(
            {
                "name": "kirocrew-team-lead",
                "tools": ["@kirocrew-dashboard"],
                "allowedTools": ["@kirocrew-work/work_ledger_read"],
            }
        )
        assert reason == "it declares no mcpServers map"

    def test_a_foreign_spec_with_a_malformed_grant_list_is_still_declined(
        self, tmp_path, monkeypatch
    ):
        """Attribution runs BEFORE grant-shape validation, and this is the case
        that proves it. The hardened reader rejects a non-list ``allowedTools`` as
        bytes it cannot use, which a hand-edit typo produces on a file that is
        otherwise entirely the operator's. Reading that as "no spec here" would
        skip attribution and overwrite the prompt, servers and model pin whole."""
        foreign = {
            "name": "my-own-lead",
            "prompt": "file:///somewhere/my-own-lead.md",
            "model": "my-pinned-model",
            "mcpServers": {"my-server": {"command": "x", "args": []}},
            "allowedTools": None,
        }
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        target.write_text(json.dumps(foreign), encoding="utf-8")
        _stub_environment(tmp_path, monkeypatch)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        after = json.loads(target.read_text(encoding="utf-8"))
        # The prompt, the model pin and the server are still theirs, and so is the typo:
        # a decline removes auto-approval, it does not repair a hand-edit.
        assert _without_gate_skips(after) == _without_gate_skips(
            foreign
        ), "the operator's file was rewritten"
        assert after["allowedTools"] is None, "a malformed grant list was not a grant to take"

    def test_a_transient_read_in_the_attribution_gap_holds_rather_than_writes(
        self, tmp_path, monkeypatch
    ):
        """The narrow case the attribution read opens, and why it is a HOLD.

        The file parses, so the first read answers "cannot use these bytes" on
        grant shape alone; the attribution read then hits an I/O error. Treating
        that as "nothing to attribute" would overwrite a file that had just
        demonstrated it is somebody's. A transient failure is the reader's own
        other class, so it holds, and a hold costs one rebuild.
        """
        foreign = {
            "name": "my-own-lead",
            "prompt": "file:///somewhere/my-own-lead.md",
            "allowedTools": None,
        }
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        raw = json.dumps(foreign)
        target.write_text(raw, encoding="utf-8")
        _stub_environment(tmp_path, monkeypatch)

        # ONLY the attribution read fails. Failing both would hold for the first
        # read's own reason and prove nothing about this gap, which is the trap
        # this test exists inside: the two reads are told apart by the
        # ``operation`` each passes, so the real reader still serves the first.
        from kiro_crew import agent_discovery

        real = agent_discovery.read_agent_spec_strict
        calls: list[str] = []

        def _reader(path, *, operation, source):
            calls.append(operation)
            if operation == "team_lead_spec_attribution":
                raise OSError("input/output error")
            return real(path, operation=operation, source=source)

        monkeypatch.setattr(agent_discovery, "read_agent_spec_strict", _reader)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.HELD
        assert target.read_text(encoding="utf-8") == raw, "the operator's file was rewritten"
        assert calls == [
            "conductor_spec_regeneration",
            "team_lead_spec_attribution",
        ], f"the first read must succeed and the second must be reached: {calls}"

    def test_a_file_that_is_present_and_unparseable_is_declined(self, tmp_path, monkeypatch):
        """One missing brace is the ordinary result of a hand edit, and those bytes
        are the operator's whether or not they parse. Declining does not strand the
        agent: the remedy is documented and costs one rename, which is the
        dashboard-author contract rather than the derived worker's."""
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        raw = '{ "name": "my-own-lead", "prompt": "file:///mine.md"'
        target.write_text(raw, encoding="utf-8")
        _stub_environment(tmp_path, monkeypatch)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        assert target.read_text(encoding="utf-8") == raw, "the operator's file was rewritten"

    def test_a_present_name_with_no_readable_bytes_is_declined(self, tmp_path, monkeypatch):
        """A dangling link is not an absent file. Only ABSENT installs without a
        name to check, so a name that still exists behind the read failure is
        present with nothing to attribute."""
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        target.symlink_to(tmp_path / "nowhere.json")
        _stub_environment(tmp_path, monkeypatch)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        assert target.is_symlink(), "the link was replaced"

    def test_only_an_absent_path_installs_without_attribution(self, tmp_path, monkeypatch):
        """The control for both declines above, and the third of the three states.
        A guard that refused everything present would still have to install on an
        empty agents directory, which is every first install."""
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        assert not target.exists()
        _stub_environment(tmp_path, monkeypatch)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        assert json.loads(target.read_text(encoding="utf-8"))["name"] == "kirocrew-team-lead"

    def test_the_three_outcomes_are_told_apart_on_the_same_path(self, tmp_path, monkeypatch):
        """The control for the two decline tests above. A guard that refused
        everything would pass them both, so this requires all three answers from
        the same path: a malformed foreign spec declined, a well-formed foreign
        spec declined, and a spec this installer wrote replaced.

        The third case is the installer's OWN write rather than a hand-made file
        carrying the right marks, because the marks are a shape and not authorship:
        only bytes reproducing the recorded ownership digest are replaced."""
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        _stub_environment(tmp_path, monkeypatch)

        target.write_text(json.dumps({"name": "theirs", "allowedTools": None}), encoding="utf-8")
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED

        target.write_text(
            json.dumps({"name": "theirs", "mcpServers": {}, "allowedTools": []}),
            encoding="utf-8",
        )
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED

        target.unlink()
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        assert json.loads(target.read_text(encoding="utf-8"))["prompt"].startswith(
            "# Kiro Crew Team Lead"
        )

    def test_the_global_mcp_merge_is_pinned_off(self, tmp_path, monkeypatch):
        """``includeMcpJson`` tells kiro-cli to merge the global ``mcp.json`` ON TOP of
        this spec, and the operator's own ``agent.json`` can set it, because
        ``build_agent_config`` deep-merges that file. Every filter in this installer reads
        ``config["mcpServers"]`` and nothing else, so a server arriving from the global
        file is governed by none of them: its ``autoApprove`` is never seen by the ceiling
        strip, and kiro-cli approves such a tool locally with no permission request. The
        pin is what makes the filtered map the only map.

        Control: a template that sets neither field still gets the pin written rather than
        left absent, because absent is not the same answer to kiro-cli as false."""
        _stub_environment(tmp_path, monkeypatch)
        base = agent.build_agent_config()
        base["includeMcpJson"] = True
        base["useLegacyMcpJson"] = True
        monkeypatch.setattr(agent, "build_agent_config", lambda: json.loads(json.dumps(base)))

        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        spec = json.loads((tmp_path / TEAM_LEAD_AGENT_FILENAME).read_text(encoding="utf-8"))
        assert spec["includeMcpJson"] is False, (
            "the global mcp.json merge stayed on, so a server from that file reaches "
            "kiro-cli past every filter in this installer"
        )
        assert "useLegacyMcpJson" not in spec, "the retired spelling can re-open the merge"

        # Control: with neither field in the template the pin is still written, and the
        # spec this installer produces is otherwise the ordinary one.
        clean = agent.build_agent_config()
        clean.pop("includeMcpJson", None)
        clean.pop("useLegacyMcpJson", None)
        monkeypatch.setattr(agent, "build_agent_config", lambda: json.loads(json.dumps(clean)))
        (tmp_path / TEAM_LEAD_AGENT_FILENAME).unlink()
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        spec = json.loads((tmp_path / TEAM_LEAD_AGENT_FILENAME).read_text(encoding="utf-8"))
        assert spec["includeMcpJson"] is False
        assert "useLegacyMcpJson" not in spec
        assert "@kirocrew-work/work_brief" in spec["allowedTools"]

    def test_every_grant_this_installer_withholds_is_audited(self, tmp_path, monkeypatch):
        """Withholding a grant is a permission DECISION, and an operator reads the SEL
        feed to find out why a grant they can see in the template is not on the spec. The
        whole-server narrowing is the one that fires on an ORDINARY rebuild -- the
        template's bare ``@kirocrew-core`` is subtracted every time -- and the ceiling pass
        after it sees only the survivors, so an unaudited subtraction there leaves no trace
        anywhere.

        All three of this installer's own withholds are required in ONE run, so a feed
        that recorded two of them does not pass."""
        records: list[dict] = []

        class _Recorder:
            def log_api_access(self, **kwargs):
                records.append(kwargs)

        _stub_environment(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "sel", lambda: _Recorder())
        base = agent.build_agent_config()
        base["mcpServers"] = {**(base.get("mcpServers") or {}), "kirocrew-panel": {"command": "/p"}}
        base["allowedTools"] = [*base.get("allowedTools", []), "session_*"]
        monkeypatch.setattr(agent, "build_agent_config", lambda: json.loads(json.dumps(base)))

        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        withheld = [
            str(r.get("resources"))
            for r in records
            if r.get("operation") == "mcp_auto_approve_withheld"
        ]
        # The whole-server narrowing: present on every rebuild, and the gap this pins.
        assert any(
            "@kirocrew-core" in r and "verb by verb" in r for r in withheld
        ), f"the whole-server narrowing left no audit record: {withheld}"
        # The two that already audited, required here so this cannot pass on one event.
        assert any("session_*" in r for r in withheld), f"no record names the pattern: {withheld}"
        assert any("kirocrew-panel" in r for r in withheld), f"no record names it: {withheld}"

    def test_an_audit_failure_never_breaks_the_install(self, tmp_path, monkeypatch):
        """Same footing as every other audit here: an unwritable SEL sink costs one
        record, not the spec. The control for the test above -- an installer that raised
        out of its own audit would fail this one instead of recording anything."""

        class _Broken:
            def log_api_access(self, **kwargs):
                raise OSError("audit sink unavailable")

        _stub_environment(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "sel", lambda: _Broken())
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        assert (tmp_path / TEAM_LEAD_AGENT_FILENAME).is_file()

    def test_an_inherited_wildcard_grant_is_not_carried(self, tmp_path, monkeypatch):
        """The ceiling judges an ``allowedTools`` entry by its EXACT NAME, so a PATTERN
        escapes it silently: it is in no table and names no server, passes as an unmapped
        name, and kiro-cli then expands it over every builtin and every verb of every
        mounted server -- including ``@kirocrew-dashboard``, which this same installer
        mounts. ``session_?end`` is the sharp case: the exact spelling is withheld by name
        while the pattern that expands onto it would be auto-approved, on the one path
        that never reaches the approval gate.

        Both controls the ceiling's own behaviour depends on are asserted here, so a drop
        that took everything would fail: an exact grant the ceiling allows survives, and a
        bare ``@server`` entry is still narrowed by its own separate pass.
        """
        _stub_environment(tmp_path, monkeypatch)
        base = agent.build_agent_config()
        base["allowedTools"] = [
            *base.get("allowedTools", []),
            "session_*",
            "*_send",
            "@*",
            "@kirocrew-dashboard/session_?end",
        ]
        monkeypatch.setattr(agent, "build_agent_config", lambda: json.loads(json.dumps(base)))
        monkeypatch.setattr(agent, "_may_auto_approve", lambda ref: "session_send" not in ref)

        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        spec = json.loads((tmp_path / TEAM_LEAD_AGENT_FILENAME).read_text(encoding="utf-8"))
        allowed = spec["allowedTools"]

        for pattern in ("session_*", "*_send", "@*", "@kirocrew-dashboard/session_?end"):
            assert pattern not in allowed, (
                f"{pattern} reached the spec, so kiro-cli would expand it over a verb the "
                "ceiling withholds by name"
            )
        # Control: the exact grants this installer ships are judged by name and kept, so
        # the drop is "patterns" and not "anything the operator touched".
        assert "fs_read" in allowed
        assert "@kirocrew-work/work_brief" in allowed
        # Control: the bare ``@server`` pass still does its own separate job, which a
        # wildcard filter neither replaces nor is replaced by.
        assert "@kirocrew-core" not in allowed

    def test_a_governed_auto_approve_on_an_inherited_server_is_stripped(
        self, tmp_path, monkeypatch
    ):
        """``autoApprove`` on an ``mcpServers`` entry is the SECOND channel a call skips
        the PreToolUse gate through: kiro-cli approves such a tool locally and emits no
        permission request, so no amount of ``allowedTools`` filtering reaches it. This
        map is ADDITIVE and ``build_agent_config`` deep-merges the operator's own
        ``agent.json``, so an entry they wrote arrives with whatever ``autoApprove`` they
        gave it -- a grant no ceiling has read.

        Driven through the real governance pass by its own predicate rather than a stubbed
        strip, so the two answers are the ceiling's. Both controls are here: a governed
        server keeps nothing, an ungoverned one the operator wrote survives, and the
        ``allowedTools`` filter still does its own separate job.
        """
        from kiro_crew.platform import governance

        monkeypatch.setattr(
            governance, "may_skip_gate_now", lambda ref: not ref.startswith("@governed")
        )
        _stub_environment(tmp_path, monkeypatch)
        base = agent.build_agent_config()
        base["mcpServers"] = {
            **(base.get("mcpServers") or {}),
            "governed": {"command": "/x/g", "args": [], "autoApprove": ["delete"]},
            "friendly": {"command": "/x/f", "args": [], "autoApprove": ["read"]},
        }
        monkeypatch.setattr(agent, "build_agent_config", lambda: json.loads(json.dumps(base)))

        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        spec = json.loads((tmp_path / TEAM_LEAD_AGENT_FILENAME).read_text(encoding="utf-8"))
        servers = spec["mcpServers"]

        assert "autoApprove" not in servers["governed"], (
            "a ceiling-governed autoApprove reached the spec, so a native chat would run "
            "the denied tool without a permission request"
        )
        # The server itself stays mounted: its tools go through the approval gate, which
        # is where a per-argument ceiling rule is actually applied.
        assert "governed" in servers
        # Control: the ceiling is silent about this one and the operator wrote it, so
        # their own statement about their own tools survives. Without this a pass that
        # stripped every autoApprove would pass the assertion above.
        assert servers["friendly"]["autoApprove"] == ["read"]
        # Control: the other channel still behaves as before -- the shipped grants are
        # on the list and the template's whole-server entry is still subtracted.
        assert "@kirocrew-work/work_brief" in spec["allowedTools"]
        assert "@kirocrew-core" not in spec["allowedTools"]

    def test_an_opt_in_set_the_operator_mounted_is_not_inherited(self, tmp_path, monkeypatch):
        """An ``opt_in`` server is an assignable SET, not an always-on capability: the
        agents that need one hand-build the entry, and that IS the per-agent assignment.
        An operator mounting one on their personal ``agent.json`` has assigned it to that
        agent, so it must not ride through ``build_agent_config`` into this spec -- least
        of all ``kirocrew-panel``, which this agent's own charter says no spec may emit.

        All three surfaces, because a server reaches a session through any of them."""
        _stub_environment(tmp_path, monkeypatch)
        base = agent.build_agent_config()
        base["mcpServers"] = {**(base.get("mcpServers") or {}), "kirocrew-panel": {"command": "/p"}}
        base["tools"] = [*base.get("tools", []), "@kirocrew-panel"]
        base["allowedTools"] = [*base.get("allowedTools", []), "@kirocrew-panel"]
        monkeypatch.setattr(agent, "build_agent_config", lambda: json.loads(json.dumps(base)))

        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        spec = json.loads((tmp_path / TEAM_LEAD_AGENT_FILENAME).read_text(encoding="utf-8"))
        assert "kirocrew-panel" not in spec["mcpServers"]
        assert "@kirocrew-panel" not in spec["tools"]
        assert "@kirocrew-panel" not in spec["allowedTools"]
        # Control: the drop set is "the opt-in sets nobody assigned HERE", not "every
        # opt-in set". Asserted on the set itself, because the two this installer assigns
        # are re-added after the drop -- so their presence in the written spec would hold
        # even if the drop had taken them, and would prove nothing.
        unassignable = agent._team_lead_unassignable_servers()
        assert "kirocrew-panel" in unassignable
        for server in agent._DEFINING_SERVERS:
            assert server not in unassignable, server
        # And the entries that survive are the installer's hand-built ones: an operator
        # who mounts their own `kirocrew-work` does not get to decide how it launches.
        assert spec["mcpServers"]["kirocrew-work"]["args"] == ["mcp-work"]

    def test_a_declined_spec_carries_no_gate_skipping_grant_at_all(self, tmp_path, monkeypatch):
        """Declining to WRITE is not declining to GOVERN, and the governing is WHOLESALE.
        A declined spec is still the agent kiro-cli loads: it reads
        ``<agents dir>/<name>.json`` and honours the ``allowedTools`` and per-server
        ``autoApprove`` it finds there without reaching the PreToolUse gate. Nothing else
        narrows them -- ``require_fork_governance`` gates fork-backed starts only, and the
        fork refresh iterates recorded forks, which this is not.

        Every grant goes, and NOTHING here decides which. A pass that filtered against
        the live ceiling was wrong three times running, each time in a new way: literal
        grants only, then patterns passing the exact-name judgement as unmapped names,
        then global inheritance governed by nobody. There is nothing to decide -- the
        file's authorship could not be established, so no grant on it is anyone's
        judgement to honour. ``web_*`` under an egress-only ceiling is the case that
        makes the difference visible: a filter has to recognise the spelling, and this
        does not have to.

        The controls are what keeps it from being over-tight: the operator's prompt,
        model and resources survive, their servers stay mounted, and the outcome stays
        DECLINED rather than being promoted to WRITTEN."""
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        _stub_environment(tmp_path, monkeypatch)
        base = agent.build_agent_config()
        base["tools"] = [*base.get("tools", []), "web_fetch"]
        base["allowedTools"] = [*base.get("allowedTools", []), "web_fetch"]
        monkeypatch.setattr(agent, "build_agent_config", lambda: json.loads(json.dumps(base)))
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN

        # The operator edits in place: a prompt, a model, resources, their own server with
        # an autoApprove of its own, and a hand-written PATTERN beside the exact names.
        spec = json.loads(target.read_text(encoding="utf-8"))
        spec["prompt"] = "my own charter"
        spec["model"] = "my-pinned-model"
        spec["resources"] = ["file://notes.md"]
        spec["mcpServers"]["vendor"] = {"command": "/v", "autoApprove": ["delete"]}
        spec["allowedTools"] = [*spec["allowedTools"], "web_*"]
        # And they re-open the ambient merge, which is how an auto-approved server can
        # arrive from OUTSIDE this file: `includeMcpJson` tells kiro-cli to merge the
        # global `mcp.json` on top of the spec, entries and their `autoApprove` included.
        # Set TRUE here on purpose -- the installer's own write already pins it false, so
        # asserting the pin over an unmodified file would prove nothing.
        spec["includeMcpJson"] = True
        edited = json.dumps(spec, indent=2) + "\n"
        target.write_text(edited, encoding="utf-8")

        # An egress-only ceiling: it denies the web verb and permits everything else, so a
        # FILTER would keep most of this list and has to catch ``web_*`` by spelling.
        monkeypatch.setattr(agent, "_may_auto_approve", lambda r: r != "web_fetch")
        outcome = agent._install_team_lead_agent()
        after = json.loads(target.read_text(encoding="utf-8"))

        # (a) no allowedTools survive -- not the denied verb, not the pattern that expands
        # onto it, and not the many entries this ceiling permits.
        assert (
            after["allowedTools"] == []
        ), f"grants survived on a spec nobody can attribute: {after['allowedTools']}"
        # (b) no autoApprove survives, on any entry.
        assert all(
            "autoApprove" not in e for e in after["mcpServers"].values() if isinstance(e, dict)
        ), "an autoApprove survived, and kiro-cli approves those locally with no request"
        # (b) continued, and the half that is not in the file at all: an inherited server
        # arrives with whatever `autoApprove` the global file gave it, which emptying this
        # spec's own map cannot reach. The merge is the mechanism, so closing it is the
        # only thing that governs that grant -- and the key is ambient when true OR
        # ABSENT, so it has to be written rather than removed.
        assert after["includeMcpJson"] is False, (
            "the ambient mcp.json merge stayed open, so a global server reaches this "
            "agent with its own auto-approve and nothing in this file governs it"
        )
        # (b) continued: the SAME exemption in KAS's currency. `allowedTools` is
        # kiro-cli's spelling of "do not prompt"; a `permissions` rule with
        # `effect: allow` is KAS's, and `match` omitted means every resource
        # (src/kiro_crew/acp/kas_permissions.py). Left behind it would keep on that
        # backend exactly what emptying `allowedTools` just took away on this one.
        assert after.get("permissions") == {"rules": []}, "the KAS relay of the same grants"
        # Controls: everything the decline exists to protect is untouched.
        assert after["prompt"] == "my own charter", "the operator's edit was reverted"
        assert after["model"] == "my-pinned-model"
        assert after["resources"] == ["file://notes.md"]
        assert "vendor" in after["mcpServers"], "the server itself stays mounted"
        assert after["mcpServers"]["vendor"]["command"] == "/v"
        assert _without_gate_skips(after) == _without_gate_skips(
            json.loads(edited)
        ), "the decline changed something other than the gate-skipping grants"
        # Control: still declined, or the F8 guard has been promoted away.
        assert outcome is agent.InstallOutcome.DECLINED

    def test_the_wholesale_strip_emits_one_audit_record(self, tmp_path, monkeypatch):
        """Axis 4 on this path. Taking every grant off somebody's file is a permission
        decision like every other one here, and the operator reads the SEL feed to find
        out why a grant they wrote themselves stopped applying.

        ONE record, not one per grant, and the count is the honest part: a record per
        grant would read as the ceiling having judged each one, which is exactly what did
        not happen. The ceiling was never consulted."""
        records: list[dict] = []

        class _Recorder:
            def log_api_access(self, **kwargs):
                records.append(kwargs)

        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        foreign = {
            "name": "my-own-lead",
            "prompt": "mine",
            "allowedTools": ["web_*", "fs_read", "@kirocrew-core/task_run"],
            "mcpServers": {"vendor": {"command": "/v", "autoApprove": ["delete"]}},
        }
        target.write_text(json.dumps(foreign), encoding="utf-8")
        _stub_environment(tmp_path, monkeypatch)
        monkeypatch.setattr(agent, "sel", lambda: _Recorder())

        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        strips = [
            r
            for r in records
            if r.get("operation") == "mcp_auto_approve_withheld"
            and r.get("source") == "team-lead-declined"
        ]
        assert len(strips) == 1, f"expected exactly one record for the strip, got {strips}"
        said = str(strips[0]["resources"])
        assert "allowedTools" in said and "vendor" in said, f"the record names neither: {said}"
        # The INHERITED channel is named too. Withholding it is part of the same single
        # decision, and it is the one an operator cannot see by reading their own file.
        assert "mcp.json" in said, f"the record does not name the ambient merge: {said}"
        assert "approval gate" in said, "the record does not say where those calls go now"

    def test_a_failed_strip_holds_rather_than_settles(self, tmp_path, monkeypatch):
        """The distinction this installer already draws on the attribution READ, applied
        to the WRITE. A transient failure holds; a deterministic refusal is permanent.

        Swallowing this returned DECLINED, which the rebuild reads as "nothing is
        waiting": ``prime_ceiling_projection`` then seeds the ceiling generation as it
        does for a clean rebuild, so the baseline advances while grants nobody vouched for
        are still live on a file that never reaches the gate. The file is left exactly as
        it was, and the next pass retries it."""
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        foreign = {
            "name": "my-own-lead",
            "prompt": "mine",
            "allowedTools": ["web_fetch", "fs_read"],
            "mcpServers": {},
        }
        raw = json.dumps(foreign)
        target.write_text(raw, encoding="utf-8")
        _stub_environment(tmp_path, monkeypatch)

        def _unwritable(path, payload):
            raise OSError("no space left on device")

        monkeypatch.setattr(agent, "_atomic_json_write", _unwritable)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.HELD, (
            "a strip that could not be written was reported as a settled decline, so the "
            "baseline advances while those grants are still live"
        )
        assert target.read_text(encoding="utf-8") == raw, "the file was left half-written"

        # Control: with the write working the SAME input settles as DECLINED rather than
        # holding forever -- or every declined spec would re-enter the retry sweep hourly.
        monkeypatch.undo()
        _stub_environment(tmp_path, monkeypatch)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        assert json.loads(target.read_text(encoding="utf-8"))["allowedTools"] == []
        # And a second pass over the now-stripped file writes NOTHING: the strip is bounded
        # to an actual change, so a rebuild does not churn a file kiro-cli watches.
        settled = target.read_text(encoding="utf-8")
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        assert target.read_text(encoding="utf-8") == settled, "the strip rewrote a clean file"

    def test_an_in_place_edit_is_declined_rather_than_silently_reverted(
        self, tmp_path, monkeypatch
    ):
        """An operator who opens the installed file and changes its prompt or its model
        leaves the declared name and both mounted servers exactly as they were, and holds
        no lineage record. On the marks alone that file reads as this installer's own and
        is rewritten whole on the next rebuild, so EVERY in-place edit would be silently
        reverted -- ordinary use, not a forge.

        Authorship is therefore the digest the installer records for the bytes it lands.
        The control that matters most is the second one: the installer's own unmodified
        write must still be replaceable, because an over-tight rule here means the agent
        can never be updated again."""
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        _stub_environment(tmp_path, monkeypatch)

        # Control: an absent path installs and records its ownership digest.
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        assert agent_state.get_managed_digest("kirocrew-team-lead"), "no ownership recorded"

        # Control: the installer's own unmodified write is replaced on the next rebuild, so
        # the digest does not freeze the spec against future releases.
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN

        # The finding: an in-place edit that leaves both marks intact.
        spec = json.loads(target.read_text(encoding="utf-8"))
        assert spec["name"] == "kirocrew-team-lead"
        for server in agent._DEFINING_SERVERS:
            assert server in spec["mcpServers"], server
        spec["prompt"] = "my own charter"
        edited = json.dumps(spec, indent=2) + "\n"
        target.write_text(edited, encoding="utf-8")

        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        after = json.loads(target.read_text(encoding="utf-8"))
        assert after["prompt"] == "my own charter", "the operator's edit was reverted"
        # Their file keeps everything EXCEPT the ability to skip the gate, which is what a
        # spec nobody can attribute does not get to carry.
        assert after["allowedTools"] == []
        assert _without_gate_skips(after) == _without_gate_skips(
            json.loads(edited)
        ), "the decline changed something other than the gate-skipping grants"

    def test_a_recorded_private_copy_is_declined_though_both_marks_hold(
        self, tmp_path, monkeypatch
    ):
        """Both marks can arrive without anyone forging them. A private copy is named after
        the crew that owns it and its declared ``name`` is set to that stem, so a crew named
        for this agent lands a file declaring this agent's name; copy any conductor and that
        file mounts both defining servers too. The lineage record is the one signal no spec
        field carries, so it is read first and the crew's own bytes are left alone.

        Driven against a file this installer DID write, so the record is the only thing
        that changes the answer and is shown to outrank even a confirmed ownership digest:
        a crew's lineage on this name means hands off whoever wrote the bytes."""
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        _stub_environment(tmp_path, monkeypatch)

        # Control: our own write, replaced on the next rebuild, so the decline below is the
        # record's doing and not some other rejection of this file.
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN
        raw = target.read_text(encoding="utf-8")

        agent_state.set_fork_info(
            "kirocrew-team-lead", forked_from="kirocrew-conductor", private_to="a-crew"
        )
        assert agent._install_team_lead_agent() is agent.InstallOutcome.DECLINED
        # Byte-identical, and this is the ONE declined class the strip leaves alone. A
        # recorded fork is already governed -- ``fork_refresh`` projects the ceiling onto
        # each recorded fork's grants -- and stripping it would break that: these bytes
        # are this installer's own write, with a matching ownership digest, so rewriting
        # them under the record leaves a digest that can never match again and the
        # control below could never pass.
        assert target.read_text(encoding="utf-8") == raw, "the crew's private copy was rewritten"

        # Control: clearing the record restores the write, so the decline tracks the record
        # rather than latching on the first refusal.
        agent_state.clear_fork_info("kirocrew-team-lead")
        assert agent._install_team_lead_agent() is agent.InstallOutcome.WRITTEN

    def test_a_lineage_read_that_may_succeed_later_holds_rather_than_writes(
        self, tmp_path, monkeypatch
    ):
        """The record is a SECOND read, so it has the spec read's two classes. A sidecar that
        cannot be read is not a file nobody claims: collapsing the two would replace a crew's
        copy on one failed read, so it holds and the next rebuild asks again."""
        target = tmp_path / TEAM_LEAD_AGENT_FILENAME
        raw = json.dumps(
            {
                "name": "kirocrew-team-lead",
                "mcpServers": {s: {} for s in agent._DEFINING_SERVERS},
                "allowedTools": [],
            }
        )
        target.write_text(raw, encoding="utf-8")
        _stub_environment(tmp_path, monkeypatch)

        def _unreadable(_name, *, strict=False):
            raise OSError("input/output error")

        monkeypatch.setattr(agent_state, "get_fork_info", _unreadable)
        assert agent._install_team_lead_agent() is agent.InstallOutcome.HELD
        assert target.read_text(encoding="utf-8") == raw, "the operator's file was rewritten"

    def test_an_absent_path_is_the_one_state_with_nothing_to_attribute(self):
        """``None`` means absent, and absent is the only answer that writes. A
        present file that does not parse never reaches this function: the caller
        declines it before asking, because unreadable bytes at this name are still
        somebody's."""
        assert agent._foreign_team_lead_spec_reason(None) is None

    def test_a_hand_edited_container_does_not_raise_out_of_the_check(self):
        """The map is shape-checked before it is read, so a wrong-typed value is
        a declined write rather than an error out of an attribution."""
        for bad in (1, "kirocrew-dashboard", ["kirocrew-dashboard"]):
            reason = agent._foreign_team_lead_spec_reason(
                {"name": "kirocrew-team-lead", "mcpServers": bad}
            )
            assert reason == "it declares no mcpServers map", bad
        # A map that IS one, missing a mount, names the mount.
        reason = agent._foreign_team_lead_spec_reason(
            {"name": "kirocrew-team-lead", "mcpServers": {"kirocrew-work": {}}}
        )
        assert reason is not None and "kirocrew-dashboard" in reason


class TestTeamLeadCanDoTheWorkItself:
    """Capability 1, which is the gap the crewmate exists to close. Asserted
    against the emitted tool list, never against a sentence about it."""

    def test_the_writers_and_shell_are_mounted(self, tmp_path, monkeypatch):
        data = _install(tmp_path, monkeypatch)
        for tool in ("fs_write", "execute_bash", "code"):
            assert tool in data["tools"], tool

    def test_the_tool_list_is_not_a_conductor_literal(self, tmp_path, monkeypatch):
        """Each conductor installer OVERWRITES the template's list with a literal
        that withholds the writers. This installer appends to the template, which
        is what makes the do-it-yourself half true of the spec."""
        data = _install(tmp_path, monkeypatch)
        assert "fs_write" in data["tools"]
        assert "@kirocrew-dashboard" in data["tools"]
        assert "@kirocrew-work" in data["tools"]

    def test_writing_a_file_still_passes_the_gate(self, tmp_path, monkeypatch):
        """Mounted and ungranted, which is the default agent's own posture:
        ``fs_write`` and ``execute_bash`` reach the PreToolUse gate rather than
        skipping it through ``allowedTools``, whose entries are name-scoped with
        no argument matching."""
        allowed = set(_install(tmp_path, monkeypatch)["allowedTools"])
        assert "fs_write" not in allowed
        assert "execute_bash" not in allowed


class TestTeamLeadGrantsAreExactlyTheIntendedTuple:
    def test_every_grant_comes_from_an_existing_tuple(self, tmp_path, monkeypatch):
        """No grant name of this spec's own. Each tuple keeps its own invariant
        comment as the justification, and a reviewer asking what a new
        auto-approve widens has nothing to be shown."""
        data = _install(tmp_path, monkeypatch)
        shipped = {ref for tuple_ in agent._TEAM_LEAD_SHIPPED_GRANTS for ref in tuple_}
        # ``fs_read`` is the template's, and survives; its ``@kirocrew-core`` does
        # not, because this spec grants that server verb by verb.
        assert set(data["allowedTools"]) == shipped | {"fs_read"}
        # Named explicitly, because the comparison above reads the shipped list and
        # so cannot notice a tuple being added to it. ``work_report`` writes into a
        # PARENT's record across a dispatch relationship, which is why every
        # conductor withholds it, and this agent is the root of its own goal and has
        # no parent to report to. ``work_brief`` is granted and is the control: it
        # proves the probe reads the work server's refs rather than matching nothing.
        assert "@kirocrew-work/work_report" not in data["allowedTools"]
        assert "@kirocrew-work/work_brief" in data["allowedTools"]

    def test_the_grant_list_documents_exactly_the_tuples_it_ships(self):
        """A tuple added to the shipped list without a line in the comment above it
        is how a withheld verb arrives unnoticed: the enumeration test reads that
        same list, so only the documentation disagrees. Keep the two in step."""
        path = _REPO_ROOT / "src" / "kiro_crew" / "agent_materialization" / "team_lead_agent.py"
        source = path.read_text(encoding="utf-8")
        # Every lookup below is checked before it is used. This test reads SOURCE
        # TEXT, so a reword of either anchor is a likely future edit, and an
        # unchecked ``index`` answers that edit with a ValueError traceback naming
        # nothing -- which reads as a broken test rather than as a renamed anchor.
        decl = "_TEAM_LEAD_SHIPPED_GRANTS: tuple"
        comment_head = "#: The grant tuples this spec ships"
        head, found, rest = source.partition(decl)
        assert found, f"{path.name} no longer declares {decl!r}; update this test's anchor"
        assert comment_head in head, (
            f"{path.name} no longer opens its grant-list comment with {comment_head!r}; "
            "update this test's anchor"
        )
        comment = head[head.rindex(comment_head) :]
        open_at = rest.find("(")
        close_at = rest.find(")")
        assert 0 <= open_at < close_at, (
            f"the {decl!r} assignment in {path.name} is not the parenthesised tuple "
            "this test reads; update this test"
        )
        listed = [
            line.strip().rstrip(",").removeprefix("agent_mod.")
            for line in rest[open_at:close_at].splitlines()
            if line.strip().startswith("agent_mod.")
        ]
        assert len(listed) == 3, f"the shipped list names {len(listed)} tuples: {listed}"
        for name in listed:
            assert f"``{name}``" in comment, f"{name} is shipped and not documented"

    def test_the_whole_server_grant_does_not_survive_beside_the_named_verbs(
        self, tmp_path, monkeypatch
    ):
        """The narrowing has to be real. Both backends resolve a whole-server
        reference before a per-tool one, so a bare ``@kirocrew-core`` left beside
        the named verbs would auto-approve every verb on that server -- including
        the ones that START work from ingested context -- and would survive the
        governance ceiling's strip of any single verb."""
        data = _install(tmp_path, monkeypatch)
        allowed = set(data["allowedTools"])
        assert "@kirocrew-core" not in allowed
        assert "@kirocrew-core/monitor_start" in allowed
        for never in (
            "@kirocrew-core/task_run",
            "@kirocrew-core/workflow_run",
            "@kirocrew-core/spawn_run",
            "@kirocrew-core/cron_add",
        ):
            assert never not in allowed, never
        # Mounted, so a governed verb still works through the approval gate.
        assert "@kirocrew-core" in data["tools"]

    def test_narrowing_keeps_a_per_verb_entry_on_the_same_server(self):
        """Only an EXACT whole-server match is dropped. A control, because a pass
        that dropped every ref containing the server name would silently remove
        the grants this spec exists to ship."""
        kept = agent._narrow_whole_server_grants(
            ["@kirocrew-core", "@kirocrew-core/monitor_start", "fs_read", "@builder-mcp"]
        )
        assert kept == ["@kirocrew-core/monitor_start", "fs_read", "@builder-mcp"]

    def test_the_dispatch_and_patrol_verbs_are_granted(self, tmp_path, monkeypatch):
        """An unattended patrol cycle must not stall on an approval nobody is
        there to give."""
        allowed = set(_install(tmp_path, monkeypatch)["allowedTools"])
        for ref in (
            "@kirocrew-dashboard/session_create",
            "@kirocrew-dashboard/chat_folder_file_self",
            "@kirocrew-core/monitor_start",
            "@kirocrew-core/resource_status",
            "@kirocrew-core/session_ledger_record",
            "@kirocrew-work/work_ledger_record",
            "@kirocrew-work/work_brief",
        ):
            assert ref in allowed, ref

    def test_the_peer_mutating_verbs_stay_gated_on_the_spec(self, tmp_path, monkeypatch):
        """The three verbs that MUTATE a peer session are earned by an ownership
        fence a spec does not have: ``authorize_target`` refuses a MEMBER caller
        on a session it did not create. A crewmate's own thread is granted them
        from that fence at session establishment, so the spec withholds them."""
        allowed = set(_install(tmp_path, monkeypatch)["allowedTools"])
        for ref in (
            "@kirocrew-dashboard/session_send",
            "@kirocrew-dashboard/session_broadcast",
            "@kirocrew-dashboard/session_stop",
            "@kirocrew-dashboard",
        ):
            assert ref not in allowed, ref
        assert "@kirocrew-dashboard" in _install(tmp_path, monkeypatch)["tools"]

    def test_the_panel_server_is_never_emitted(self, tmp_path, monkeypatch):
        """A spec may not carry it at all: it is ``opt_in`` and host-injected per
        session, and its verbs resolve their crew from the session. A spec that
        listed them would ship tools answering ``no_crew``."""
        data = _install(tmp_path, monkeypatch)
        assert "@kirocrew-panel" not in data["tools"]
        assert "kirocrew-panel" not in data["mcpServers"]
        assert not [ref for ref in data["allowedTools"] if "kirocrew-panel" in ref]

    def test_the_ceiling_filters_the_grants_and_the_derived_rules(self, tmp_path, monkeypatch):
        """``allowedTools`` is the one path that never reaches the gate, so a host
        governing a verb gets a prompt rather than a bypass -- and the KAS rules
        are derived from the FILTERED list, so a stripped grant loses its rule."""
        data = _install(
            tmp_path,
            monkeypatch,
            may_auto_approve=lambda ref: ref != "@kirocrew-core/monitor_start",
        )
        assert "@kirocrew-core/monitor_start" not in data["allowedTools"]
        rules = json.dumps(data["permissions"])
        assert "monitor_start" not in rules
        assert "monitor_update" in rules

    def test_the_two_opt_in_servers_are_assigned_by_hand(self, tmp_path, monkeypatch):
        """Neither spec-writing loop emits an ``opt_in`` set, so naming them here
        IS the per-agent assignment. The template's own servers survive, because
        an agent that runs a build may need them."""
        mcp = _install(tmp_path, monkeypatch)["mcpServers"]
        assert mcp["kirocrew-dashboard"]["args"] == ["mcp-dashboard"]
        assert mcp["kirocrew-work"]["args"] == ["mcp-work"]
        assert "kirocrew-core" in mcp


class TestTeamLeadCharterStatesEachCapability:
    """One assertion per capability the crewmate ships, because for a charter the
    sentence IS the mechanism: an agent that is not told to call the evaluator
    reads the claim instead."""

    def test_it_decides_between_doing_and_dispatching(self, tmp_path, monkeypatch):
        charter = _charter(tmp_path, monkeypatch)
        assert "ONE acceptance condition" in charter
        assert "Everything else is dispatched." in charter

    def test_it_registers_the_work_before_starting(self, tmp_path, monkeypatch):
        """Generic by design: a step the owner configures, never a named crew."""
        charter = _charter(tmp_path, monkeypatch)
        assert "run the owner's intake step" in charter

    def test_it_dispatches_in_the_mandated_order(self, tmp_path, monkeypatch):
        charter = _charter(tmp_path, monkeypatch)
        create = charter.index("`work_ledger_record` `action=create`")
        dispatch = charter.index("`session_create` with a title")
        bind = charter.index("`action=bind`")
        seed = charter.index("`session_send` the seed")
        assert create < dispatch < bind < seed
        assert "Bind before you seed." in charter
        assert "ONE brief file that every seed names by path" in charter

    def test_nesting_is_the_default_and_is_one_level(self, tmp_path, monkeypatch):
        """The promise is graded against what the server permits: a ledger item at
        the third conducting level is refused, so a charter promising more would
        promise what the server refuses."""
        charter = _charter(tmp_path, monkeypatch)
        assert "dispatch a conductor rather than a worker" in charter
        assert "That nesting is one level" in charter

    def test_patrol_is_event_driven(self, tmp_path, monkeypatch):
        charter = _charter(tmp_path, monkeypatch)
        assert 'watch="work-ledger"' in charter

    def test_loop_health_is_a_runtime_reading_not_a_memory(self, tmp_path, monkeypatch):
        """A dead loop reads exactly like a working one, so the charter names the
        two fields the runtime DERIVES -- the ``bind`` reply's ``patrol`` and the
        compact row's ``unpatrolled`` -- and keeps ``monitor_inspect`` as the
        fallback. A rule that tells the agent to remember having armed a loop is
        the failure it is written against."""
        charter = _charter(tmp_path, monkeypatch)
        assert "Loop health is checked, never remembered" in charter
        assert "`unpatrolled`" in charter
        assert "a `patrol` field" in charter
        assert "`monitor_inspect` is the fallback" in charter

    def test_acceptance_is_the_evaluator_and_never_a_claim(self, tmp_path, monkeypatch):
        charter = _charter(tmp_path, monkeypatch)
        assert "scripts/accept_eval.py" in charter
        assert "`action=decide`" in charter
        assert "`action=accept`" in charter
        assert "a CLAIM, never an acceptance" in charter
        assert "Nothing a child can write reaches `verdict`" in charter

    def test_the_head_sha_is_read_in_the_turn_it_is_reported(self, tmp_path, monkeypatch):
        """A child's green is a reading of some head, and a rebase moves it."""
        charter = _charter(tmp_path, monkeypatch)
        assert "names the head sha you read from git in that same turn" in charter

    def test_a_stalled_fleet_is_not_read_as_a_busy_one(self, tmp_path, monkeypatch):
        charter = _charter(tmp_path, monkeypatch)
        assert "`stale` at once is a STOPPED fleet" in charter

    def test_the_seed_quotes_the_ask_and_the_child_echoes_it(self, tmp_path, monkeypatch):
        """Both halves, because one without the other does not catch the failure:
        a verbatim seed nobody reads back is still a seed nobody read."""
        charter = _charter(tmp_path, monkeypatch)
        assert "restates the owner's ask VERBATIM" in charter
        assert "Require the echo." in charter

    def test_it_drives_the_dashboard_and_writes_no_numbers(self, tmp_path, monkeypatch):
        charter = _charter(tmp_path, monkeypatch)
        assert "`dashboard_fields`" in charter
        assert "`dashboard_write`" in charter
        assert "`verdict`" in charter
        assert "`for_you`" in charter
        assert "Write judgement, never arithmetic." in charter

    def test_its_own_state_survives_a_restart(self, tmp_path, monkeypatch):
        charter = _charter(tmp_path, monkeypatch)
        assert "`session_ledger_record`" in charter
        assert "a compaction or a restart resumes the patrol" in charter

    def test_capacity_is_read_and_never_a_number_it_holds(self, tmp_path, monkeypatch):
        charter = _charter(tmp_path, monkeypatch)
        assert "`resource_status`" in charter
        assert "hold no count of your own for how many sessions a goal may run" in charter

    def test_the_charter_carries_no_polling_prose(self, tmp_path, monkeypatch):
        """An event-driven patrol described as a timer is a charter that teaches
        the opposite of what it mounts. The control proves the probe reads."""
        charter = _charter(tmp_path, monkeypatch).lower()
        for banned in ("poll", "on a timer", "re-check in"):
            assert banned not in charter, banned
        assert "work-ledger" in charter

    def test_the_charter_names_its_own_skill_as_the_procedure(self, tmp_path, monkeypatch):
        """Wiring, not wording. A charter pointing at a conductor's skill would
        send this agent to a procedure whose first rule is that it does no work
        itself, which is the one capability it exists to add. The shipped skill
        directory and the name the charter reads must be the same string."""
        charter = _charter(tmp_path, monkeypatch)
        assert "The `team-lead` skill carries the operating procedure" in charter
        assert (_SKILLS / "team-lead" / "SKILL.md").is_file()
        # The two scripts still come from where they are maintained.
        assert "`goal-conductor` skill's `scripts/accept_eval.py`" in charter

    def test_the_retired_verbosity_token_is_absent(self, tmp_path, monkeypatch):
        """Reply style arrives as session-context chrome for every agent, so a
        token left here reaches the model as a literal."""
        assert "{{VERBOSITY_BLOCK}}" not in _install(tmp_path, monkeypatch)["prompt"]


class TestTheCrewmateIsMadeByOneDocumentedCommand:
    """The product ships the TEMPLATE. Binding a crewmate to it is the operator's
    one command, and these tests pin that the command documented is the command
    the CLI actually accepts -- a doc naming a flag the parser does not take is
    worse than no doc, because it fails only when somebody follows it."""

    def test_the_cli_accepts_the_documented_command(self):
        """Read off the parser rather than trusted: ``--name`` is required and
        ``--kiro-agent`` takes the template name."""
        cli = (_REPO_ROOT / "src" / "kiro_crew" / "cli.py").read_text(encoding="utf-8")
        assert 'agent_sub.add_parser("create"' in cli
        assert 'agent_create.add_argument("--kiro-agent"' in cli
        assert '"--name",\n        required=True,' in cli

    def test_the_rfc_and_the_skill_both_name_it(self):
        """Both readers of this feature need it, and neither should invent its own
        spelling: an operator reading the RFC and an agent reading the skill."""
        command = "kirocrew agent create --name <name> --kiro-agent kirocrew-team-lead"
        rfc = (_REPO_ROOT / "docs" / "request-for-change" / "rfc-lead-crewmate.md").read_text(
            encoding="utf-8"
        )
        skill = (_SKILLS / "team-lead" / "SKILL.md").read_text(encoding="utf-8")
        assert command in rfc
        assert command in skill

    def test_no_config_switch_and_no_boot_path_provisioning_remain(self):
        """The product creates no crewmate, so nothing may read a switch for one
        and nothing may run on the boot path for one. Asserted against the three
        files that carried it, with a control proving each probe reads."""
        for rel, control in (
            (("src", "kiro_crew", "config", "sections.py"), "crew_panel"),
            (("src", "kiro_crew", "config", "loader.py"), "crew_panel"),
            (("src", "kiro_crew", "dashboard", "server.py"), "_kick_crewmate_prune"),
        ):
            text = (_REPO_ROOT.joinpath(*rel)).read_text(encoding="utf-8")
            assert control in text, f"{rel[-1]}: the probe does not read"
            assert "team_lead_crewmate" not in text, rel[-1]


class TestTeamLeadSkillReusesRatherThanCopies:
    def test_the_skill_ships_in_the_tree(self):
        assert (_SKILLS / "team-lead" / "SKILL.md").is_file()

    def test_it_delegates_the_shared_procedure_instead_of_restating_it(self):
        """The skill is a DELTA. Dispatch order, patrol, acceptance, stop
        conditions, durable state and capacity live in ``goal-conductor``, and a
        procedure stated twice is one that drifts until a reader follows neither
        copy. So the test is that the pointer is there and the restatement is
        not."""
        skill = (_SKILLS / "team-lead" / "SKILL.md").read_text(encoding="utf-8")
        assert "goal-conductor" in skill
        assert "It is your procedure" in skill
        # The sections it defers rather than repeats, each named by a phrase the
        # long form used. A control follows, so an empty match cannot pass.
        for restated in ("`action=bind`", "`monitor_start`", "`action=verdict`"):
            assert restated not in skill, restated
        assert "do-it-yourself test" in skill

    def test_no_copy_of_either_script_ships_with_it(self):
        """The reuse is the point. A copy is a second file whose later divergence
        from the original nothing in the tree would detect."""
        for script in ("accept_eval.py", "patrol_budget.py"):
            assert not (_SKILLS / "team-lead" / "scripts" / script).exists(), script

    def test_the_skill_carries_no_polling_prose(self):
        """The control is a term this delta carries itself. Patrol belongs to
        ``goal-conductor``, so a term from the patrol section would prove nothing
        about whether this probe reads."""
        skill = (_SKILLS / "team-lead" / "SKILL.md").read_text(encoding="utf-8").lower()
        for banned in ("on a timer", "re-check in", "poll"):
            assert banned not in skill, banned
        assert "goal-conductor" in skill
