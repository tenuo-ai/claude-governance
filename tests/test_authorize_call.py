"""authorize_call routing — the three-layer decision, without a live authorizer.

We monkeypatch the network calls (`authorize`, `authorize_with_approval`) to
capture how each call is ROUTED: which capability, which warrant. That's the
security-critical logic — that an in-subagent call runs under the child warrant,
a spawn runs through the signed gate, an undeclared subagent is denied fail-closed.
"""
from __future__ import annotations

import pytest


def test_spawn_flat_coverage_when_no_roles(cli_mod, make_cfg):
    # No subagents declared -> spawning is plain orchestration, audited not denied.
    allowed, reason, governed, tool = cli_mod.authorize_call(
        make_cfg(), "Agent", {"subagent_type": "anything"}, None, {})
    assert allowed is True
    assert governed is False
    assert tool == "Agent"
    assert "flat coverage" in reason


def test_spawn_gate_routes_to_spawn_cap(cli_mod, make_cfg, monkeypatch):
    captured = {}

    def fake_authorize(tenuo_tool, route, sign_args, body=None, warrant_b64=None):
        captured.update(tenuo_tool=tenuo_tool, route=route, sign_args=sign_args)
        return True, "ok"

    monkeypatch.setattr(cli_mod, "authorize", fake_authorize)
    roles = {"researcher": {"tools": ["Read"]}}
    allowed, reason, governed, tool = cli_mod.authorize_call(
        make_cfg(), "Agent", {"subagent_type": "researcher"}, None, roles)
    assert allowed is True
    assert tool == cli_mod.SPAWN_CAP == "spawn_agent"
    assert captured["tenuo_tool"] == "spawn_agent"
    assert captured["route"] == "/verify/spawn_agent"
    assert captured["sign_args"] == {"subagent_type": "researcher"}


def test_undeclared_subagent_denied_fail_closed(cli_mod, make_cfg, bound):
    # agent_type set, but not a declared role -> deny without any network call.
    roles = {"researcher": {"tools": ["Read"]}}
    cfg = make_cfg(enforce={"Read": "subpath:" + str(bound)}, _sandbox_abs=str(bound))
    allowed, reason, governed, tool = cli_mod.authorize_call(
        cfg, "Read", {"file_path": str(bound / "x")}, "deployer", roles)
    assert allowed is False
    assert "undeclared subagent 'deployer'" in reason


def test_in_subagent_uses_child_warrant(cli_mod, make_cfg, bound, monkeypatch):
    # A declared role with a minted child warrant -> that warrant is presented.
    roles = {"researcher": {"tools": ["Read"]}}
    (bound / ".state").mkdir(parents=True, exist_ok=True)
    cli_mod.subwarrant_path("researcher").write_text("CHILD_STACK")

    captured = {}

    def fake_awa(cfg, claude_tool, tenuo_tool, route, sign_args, body, warrant_b64, live):
        captured.update(warrant_b64=warrant_b64, tenuo_tool=tenuo_tool, live=live)
        return True, "ok", {}

    monkeypatch.setattr(cli_mod, "authorize_with_approval", fake_awa)
    cfg = make_cfg(enforce={"Read": "subpath:" + str(bound)}, _sandbox_abs=str(bound))
    allowed, reason, governed, tool = cli_mod.authorize_call(
        cfg, "Read", {"file_path": str(bound / "x")}, "researcher", roles, live=False)
    assert allowed is True
    assert governed is True
    assert captured["warrant_b64"] == "CHILD_STACK"   # child, not the session warrant
    assert captured["tenuo_tool"] == "read_file"


def test_skip_approval_gate_uses_plain_authorize(cli_mod, make_cfg, monkeypatch):
    calls = {"authorize": 0, "awa": 0}
    monkeypatch.setattr(cli_mod, "authorize",
                        lambda *a, **k: (calls.__setitem__("authorize", calls["authorize"] + 1), (True, "ok"))[1])
    monkeypatch.setattr(cli_mod, "authorize_with_approval",
                        lambda *a, **k: (calls.__setitem__("awa", calls["awa"] + 1), (True, "ok"))[1])
    cfg = make_cfg(enforce={"Bash": "shlex:ls"})
    cli_mod.authorize_call(cfg, "Bash", {"command": "ls"}, None, {}, skip_approval_gate=True)
    assert calls["authorize"] == 1
    assert calls["awa"] == 0
