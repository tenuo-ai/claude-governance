"""Preflight / wiring checks (PyPI layout — no repo ``bin/tenuo-claude``)."""

from __future__ import annotations

import argparse

from tenuo_claude_code import cli


def test_check_wiring_ok_without_launcher(monkeypatch, tmp_path):
    """Missing ``./bin/tenuo-claude`` must not fail preflight (PyPI installs use PATH)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(cli, "LAUNCHER", proj / "bin" / "tenuo-claude", raising=False)
    monkeypatch.setattr(cli, "DEMO_DIR", proj, raising=False)
    ok = cli._check_wiring({"mcp": {}}, True)
    assert ok is True


def test_probe_runtime_creds_accepts_tenant_without_srl(monkeypatch):
    monkeypatch.setattr(
        cli,
        "cloud_api",
        lambda method, url, key, path: (
            404,
            {"error": {"code": "srl_not_found", "message": "no SRL exists for this tenant"}},
        ),
    )

    ok, msg = cli.probe_runtime_creds({"url": "https://api.example", "api_key": "rt"})

    assert ok is True
    assert "no SRL yet" in msg


def test_cloud_onboard_writes_profile_before_preflight(monkeypatch, tmp_path):
    state = tmp_path / ".state"
    monkeypatch.setattr(cli, "DEMO_DIR", tmp_path, raising=False)
    monkeypatch.setattr(cli, "STATE", state, raising=False)
    monkeypatch.setattr(cli, "CONFIG_FILE", tmp_path / "tenuo.yaml", raising=False)
    monkeypatch.setattr(cli, "CLOUD_ENV", state / "cloud.env", raising=False)
    monkeypatch.setattr(cli, "CLOUD_STATE", state / "cloud_state.json", raising=False)
    monkeypatch.setattr(cli, "CLOUD_PROFILE", tmp_path / "tenuo.cloud.yaml", raising=False)
    monkeypatch.setattr(cli, "ADVANCED_PROFILE", tmp_path / "tenuo.advanced.yaml", raising=False)
    monkeypatch.setattr(cli, "ADMIN_ENV", tmp_path / "home" / ".tenuo" / "admin.env", raising=False)
    monkeypatch.setattr(cli, "scaffold_example_policy", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_parse_connect_token",
                        lambda token: {"url": "https://api.example", "api_key": "rt"})

    seen = {}

    def fake_check(_args):
        seen["mode"] = cli.intended_mode({})
        raise SystemExit(0)

    monkeypatch.setattr(cli, "cmd_check", fake_check)
    monkeypatch.setattr(cli, "cmd_init", lambda _args: None)
    monkeypatch.setattr(cli, "cmd_up", lambda _args: None)
    monkeypatch.setattr(cli, "cmd_verify", lambda _args: None)

    cli.cmd_onboard(argparse.Namespace(
        cloud=True,
        local=False,
        yes=True,
        no_scaffold=False,
        connect_token="tenuo_ct_fake",
        admin_key=None,
        advanced=False,
        demo=False,
        approver=None,
        approver_id=None,
    ))

    assert seen["mode"] == "cloud"
