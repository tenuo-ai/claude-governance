"""resolve_tool / mcp_tool_name / the small policy helpers — pure logic."""
from __future__ import annotations

import os

import pytest


# --- mcp_tool_name ----------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("mcp__tenuo-files__read_file", "read_file"),
    ("mcp__server__multi_word_tool", "multi_word_tool"),  # underscores preserved
    ("Read", None),
    ("mcp__server__", None),       # empty tool segment
    ("mcp__server", None),         # only two segments
    ("", None),
])
def test_mcp_tool_name(cli_mod, name, expected):
    assert cli_mod.mcp_tool_name(name) == expected


# --- default_mode / catchall_cap -------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, "deny"), ("deny", "deny"), ("audit", "audit"),
    ("AUDIT", "audit"), ("nonsense", "deny"),
])
def test_default_mode(cli_mod, make_cfg, value, expected):
    cfg = make_cfg(default=value)
    assert cli_mod.default_mode(cfg) == expected


def test_catchall_cap(cli_mod, make_cfg):
    assert cli_mod.catchall_cap(make_cfg(default="deny")) == "unlisted"
    assert cli_mod.catchall_cap(make_cfg(default="audit")) == "audit"


# --- audit_map --------------------------------------------------------------

def test_audit_map_known_and_unknown(cli_mod, make_cfg):
    cfg = make_cfg(audit=["WebSearch", "CustomThing"])
    m = cli_mod.audit_map(cfg)
    assert m["WebSearch"] == "web_search"      # from TOOL_DEFAULTS
    assert m["CustomThing"] == "customthing"   # slug() fallback


# --- subagent_roles / webfetch_approval ------------------------------------

def test_subagent_roles(cli_mod, make_cfg):
    assert cli_mod.subagent_roles(make_cfg()) == {}
    roles = {"researcher": {"tools": ["Read"]}}
    assert cli_mod.subagent_roles(make_cfg(subagents=roles)) == roles


def test_webfetch_approval(cli_mod, make_cfg):
    assert cli_mod.webfetch_approval(make_cfg()) is None
    # plain string policy -> not a dict -> no approval block
    assert cli_mod.webfetch_approval(make_cfg(enforce={"WebFetch": "x"})) is None
    # structured, no approval
    assert cli_mod.webfetch_approval(
        make_cfg(enforce={"WebFetch": {"domains": ["a.com"]}})) is None
    # structured, with approval
    appr = {"threshold": 2}
    got = cli_mod.webfetch_approval(
        make_cfg(enforce={"WebFetch": {"domains": ["a.com"], "approval": appr}}))
    assert got == appr


# --- governed_map -----------------------------------------------------------

def test_governed_map_unknown_tool_raises(cli_mod, make_cfg):
    with pytest.raises(SystemExit):
        cli_mod.governed_map(make_cfg(enforce={"Frobnicate": "subpath:/x"}))


def test_governed_map_structured_only_for_webfetch(cli_mod, make_cfg):
    with pytest.raises(SystemExit):
        cli_mod.governed_map(make_cfg(enforce={"Read": {"domains": ["a.com"]}}))


def test_governed_map_webfetch_structured(cli_mod, make_cfg):
    gov = cli_mod.governed_map(make_cfg(enforce={"WebFetch": {"domains": ["a.com"]}}))
    assert gov["WebFetch"]["cap"] == "web_fetch"
    assert "web" in gov["WebFetch"]


# --- resolve_tool -----------------------------------------------------------

def test_resolve_tool_enforced_subpath_realpaths(cli_mod, make_cfg, tmp_path):
    sb = str(tmp_path)
    cfg = make_cfg(enforce={"Read": "subpath:" + sb}, _sandbox_abs=sb)
    target = tmp_path / "notes.txt"
    cap, route, sign_args, body, governed = cli_mod.resolve_tool(
        cfg, "Read", {"file_path": str(target)})
    assert cap == "read_file"
    assert route == "/verify/read_file"
    assert governed is True
    assert sign_args["path"] == os.path.realpath(str(target))


def test_resolve_tool_bash_passthrough(cli_mod, make_cfg):
    cfg = make_cfg(enforce={"Bash": "shlex:ls,echo"})
    cap, route, sign_args, body, governed = cli_mod.resolve_tool(
        cfg, "Bash", {"command": "ls -la"})
    assert cap == "run_command"
    assert sign_args["command"] == "ls -la"
    assert governed is True


def test_resolve_tool_webfetch_extracts_host(cli_mod, make_cfg):
    cfg = make_cfg(enforce={"WebFetch": {"domains": ["api.github.com"]}})
    cap, route, sign_args, body, governed = cli_mod.resolve_tool(
        cfg, "WebFetch", {"url": "https://api.github.com/repos"})
    assert cap == "web_fetch"
    assert sign_args == {"url": "https://api.github.com/repos", "host": "api.github.com"}
    assert governed is True


def test_resolve_tool_audit_listed(cli_mod, make_cfg):
    cfg = make_cfg(audit=["WebSearch"])
    cap, route, sign_args, body, governed = cli_mod.resolve_tool(
        cfg, "WebSearch", {"query": "x"})
    assert cap == "web_search"
    assert governed is False
    assert sign_args == {}            # audit tools carry no constraint args
    assert body == {"query": "x"}


def test_resolve_tool_catchall_deny(cli_mod, make_cfg):
    cfg = make_cfg(default="deny")
    cap, route, sign_args, body, governed = cli_mod.resolve_tool(
        cfg, "NotebookEdit", {"notebook_path": "x.ipynb"})
    assert cap == "unlisted"          # ungranted -> signed DENY
    assert route == "/gate"
    assert governed is False


def test_resolve_tool_catchall_audit(cli_mod, make_cfg):
    cfg = make_cfg(default="audit")
    cap, *_ = cli_mod.resolve_tool(cfg, "SomethingNew", {})
    assert cap == "audit"             # granted -> allow+log


def test_resolve_tool_mcp_bare_name(cli_mod, make_cfg, tmp_path):
    cfg = make_cfg(mcp={"enforce": {"read_file": "subpath:/sb"}})
    cap, route, sign_args, body, governed = cli_mod.resolve_tool(
        cfg, "read_file", {"path": str(tmp_path / "f")})
    assert cap == "read_file"
    assert route == "/verify/read_file"
    assert governed is True


def test_resolve_tool_mcp_prefixed_name(cli_mod, make_cfg, tmp_path):
    cfg = make_cfg(mcp={"enforce": {"read_file": "subpath:/sb"}})
    cap, route, sign_args, body, governed = cli_mod.resolve_tool(
        cfg, "mcp__tenuo-files__read_file", {"path": str(tmp_path / "f")})
    assert cap == "read_file"
    assert governed is True


def test_resolve_tool_mcp_unenforced_falls_to_catchall(cli_mod, make_cfg):
    # delete_deployment is intentionally NOT in mcp.enforce -> default-deny.
    cfg = make_cfg(mcp={"enforce": {"read_file": "subpath:/sb"}}, default="deny")
    cap, route, *_ , governed = cli_mod.resolve_tool(
        cfg, "mcp__tenuo-files__delete_deployment", {"target": "production"})
    assert cap == "unlisted"
    assert route == "/gate"
    assert governed is False
