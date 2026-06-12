"""agent_definitions / resolve_subagent_role — role <-> subagent_type linkage."""
from __future__ import annotations


def _write_agent(agents_dir, filename, frontmatter_name=None):
    agents_dir.mkdir(parents=True, exist_ok=True)
    if frontmatter_name is None:
        body = "no frontmatter here\n"
    else:
        body = f"---\nname: {frontmatter_name}\ndescription: x\n---\nbody\n"
    (agents_dir / filename).write_text(body)


def test_agent_definitions_uses_frontmatter_name(cli_mod, bound):
    agents = bound / ".claude" / "agents"
    _write_agent(agents, "researcher.md", frontmatter_name="researcher")
    _write_agent(agents, "helper.md", frontmatter_name="bar")   # name != filename
    _write_agent(agents, "plain.md", frontmatter_name=None)     # falls back to stem

    defs = cli_mod.agent_definitions()
    assert set(defs) == {"researcher", "bar", "plain"}
    assert defs["bar"].name == "helper.md"


def test_resolve_declared_role(cli_mod, bound):
    agents = bound / ".claude" / "agents"
    _write_agent(agents, "researcher.md", frontmatter_name="researcher")
    ok, where = cli_mod.resolve_subagent_role("researcher")
    assert ok is True
    assert where.endswith("researcher.md")


def test_resolve_builtin_role(cli_mod, bound):
    ok, where = cli_mod.resolve_subagent_role("Explore")
    assert ok is True
    assert where == "built-in"


def test_resolve_unknown_role(cli_mod, bound):
    ok, where = cli_mod.resolve_subagent_role("does-not-exist")
    assert ok is False
    assert "no agent definition" in where
