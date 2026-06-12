"""Preflight / wiring checks (PyPI layout — no repo ``bin/tenuo-claude``)."""

from __future__ import annotations

from tenuo_claude_code import cli


def test_check_wiring_ok_without_launcher(monkeypatch, tmp_path):
    """Missing ``./bin/tenuo-claude`` must not fail preflight (PyPI installs use PATH)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(cli, "LAUNCHER", proj / "bin" / "tenuo-claude", raising=False)
    monkeypatch.setattr(cli, "DEMO_DIR", proj, raising=False)
    ok = cli._check_wiring({"mcp": {}}, True)
    assert ok is True
