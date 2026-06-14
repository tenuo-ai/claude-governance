"""Cloud binding probes, trigger-fire errors, and holder re-claim helpers."""

from __future__ import annotations

import argparse
import base64
import json

import pytest

from tenuo_claude_code import admin, cli


def _agent_not_allowed_body():
    return {
        "error": {
            "code": "agent_not_allowed",
            "message": "Agent not allowed to fire this trigger",
        },
    }


def test_trigger_fire_failure_message_agent_not_allowed(monkeypatch):
    monkeypatch.setattr(
        cli,
        "load_cloud_state",
        lambda: {"agent_id": "agt_test", "trigger_id": "trig_demo"},
    )
    msg = cli.trigger_fire_failure_message(403, _agent_not_allowed_body(), "trig_demo")
    assert "agt_test" in msg
    assert "trig_demo" in msg
    assert "tenuo-admin setup" in msg


def test_trigger_fire_failure_message_generic():
    msg = cli.trigger_fire_failure_message(500, {"error": "boom"}, "trig_x")
    assert "Trigger fire failed (500)" in msg
    assert "boom" in msg


def test_fire_session_warrant_raises_guided_error(monkeypatch, make_cfg):
    cfg = make_cfg(_sandbox_abs="/sandbox")

    def fake_cloud_api(method, url, key, path, body=None):
        assert path == "/v1/triggers/trig_demo/fire"
        return 403, _agent_not_allowed_body()

    monkeypatch.setattr(cli, "cloud_api", fake_cloud_api)
    monkeypatch.setattr(cli, "trigger_id", lambda _cfg: "trig_demo")
    monkeypatch.setattr(cli, "load_cloud_state", lambda: {"agent_id": "agt_test"})

    with pytest.raises(SystemExit, match="tenuo-admin setup"):
        cli.fire_session_warrant(cfg, {"url": "https://api.example", "api_key": "rt"})


def test_probe_cloud_bindings_ok(monkeypatch, make_cfg):
    cfg = make_cfg(_sandbox_abs="/sandbox")
    calls = []

    def fake_cloud_api(method, url, key, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return 200, {
                "public_key": "aa" * 32,
                "allowed_triggers": ["trig_demo"],
            }
        assert body.get("dry_run") is True
        return 200, {"dry_run": {"would_issue": True}}

    monkeypatch.setattr(cli, "cloud_api", fake_cloud_api)
    monkeypatch.setattr(cli, "load_cloud_state",
                        lambda: {"agent_id": "agt_1", "trigger_id": "trig_demo"})
    monkeypatch.setattr(cli, "trigger_id", lambda _cfg: "trig_demo")
    monkeypatch.setattr(cli, "local_holder_pub_hex", lambda: "aa" * 32)

    ok, msg = cli.probe_cloud_bindings(
        cfg, {"url": "https://api.example", "api_key": "rt"}, admin_key="admin")

    assert ok is True
    assert "dry-run OK" in msg
    assert ("GET", "/v1/agents/agt_1", None) in calls
    assert any(c[0] == "POST" and "/fire" in c[1] for c in calls)


def test_probe_cloud_bindings_stale_allowed_triggers(monkeypatch, make_cfg):
    cfg = make_cfg()

    def fake_cloud_api(method, url, key, path, body=None):
        if method == "GET":
            return 200, {
                "public_key": "bb" * 32,
                "allowed_triggers": ["trig_old"],
            }
        raise AssertionError("dry-run should not run when binding is stale")

    monkeypatch.setattr(cli, "cloud_api", fake_cloud_api)
    monkeypatch.setattr(cli, "load_cloud_state",
                        lambda: {"agent_id": "agt_1", "trigger_id": "trig_new"})
    monkeypatch.setattr(cli, "trigger_id", lambda _cfg: "trig_new")
    monkeypatch.setattr(cli, "local_holder_pub_hex", lambda: "bb" * 32)

    ok, msg = cli.probe_cloud_bindings(
        cfg, {"url": "https://api.example", "api_key": "rt"}, admin_key="admin")

    assert ok is False
    assert "trig_old" in msg
    assert "trig_new" in msg
    assert "tenuo-admin setup" in msg


def test_probe_cloud_bindings_holder_mismatch(monkeypatch, make_cfg):
    cfg = make_cfg()

    def fake_cloud_api(method, url, key, path, body=None):
        return 200, {
            "public_key": "cc" * 32,
            "allowed_triggers": ["trig_demo"],
        }

    monkeypatch.setattr(cli, "cloud_api", fake_cloud_api)
    monkeypatch.setattr(cli, "load_cloud_state",
                        lambda: {"agent_id": "agt_1", "trigger_id": "trig_demo"})
    monkeypatch.setattr(cli, "trigger_id", lambda _cfg: "trig_demo")
    monkeypatch.setattr(cli, "local_holder_pub_hex", lambda: "dd" * 32)

    ok, msg = cli.probe_cloud_bindings(
        cfg, {"url": "https://api.example", "api_key": "rt"}, admin_key="admin")

    assert ok is False
    assert "holder key mismatch" in msg
    assert "tenuo-admin setup" in msg


def test_probe_cloud_bindings_dry_run_agent_not_allowed_without_admin(monkeypatch, make_cfg):
    cfg = make_cfg(_sandbox_abs="/sandbox")

    def fake_cloud_api(method, url, key, path, body=None):
        return 403, _agent_not_allowed_body()

    monkeypatch.setattr(cli, "cloud_api", fake_cloud_api)
    monkeypatch.setattr(cli, "load_cloud_state",
                        lambda: {"agent_id": "agt_1", "trigger_id": "trig_demo"})
    monkeypatch.setattr(cli, "trigger_id", lambda _cfg: "trig_demo")
    monkeypatch.setattr(cli, "local_holder_pub_hex", lambda: None)

    ok, msg = cli.probe_cloud_bindings(
        cfg, {"url": "https://api.example", "api_key": "rt"}, admin_key=None)

    assert ok is False
    assert "trig_demo" in msg
    assert "tenuo-admin setup" in msg


def test_probe_cloud_bindings_incomplete_setup(monkeypatch, make_cfg):
    monkeypatch.setattr(cli, "load_cloud_state", lambda: {"agent_id": "agt_1"})
    monkeypatch.setattr(cli, "trigger_id", lambda _cfg: None)

    ok, msg = cli.probe_cloud_bindings(
        make_cfg(), {"url": "https://api.example", "api_key": "rt"}, admin_key="admin")

    assert ok is False
    assert "incomplete" in msg


def test_refresh_subwarrants_holder_mismatch_message(monkeypatch, bound, make_cfg):
    from tenuo.exceptions import DelegationAuthorityError
    import tenuo

    cfg = make_cfg(
        subagents={"researcher": {"tools": ["Read"], "ttl_seconds": 3600}},
        enforce={"Read": "subpath:{sandbox}"},
    )
    bound.joinpath(".state").mkdir(parents=True, exist_ok=True)
    cli.WARRANT.write_text("dummy-warrant")
    cli.HOLDER_KEY.write_text(base64.b64encode(b"\x00" * 32).decode())

    class FakeWarrant:
        capabilities = {"read_file": {}}

        @classmethod
        def from_base64(cls, _raw):
            return cls()

        def attenuate_builder(self):
            return self

        def inherit_all(self):
            return self

        def with_tools(self, _caps):
            return self

        def with_intent(self, _intent):
            return self

        def with_ttl(self, _ttl):
            return self

        def delegate(self, _holder):
            raise DelegationAuthorityError(
                "signing key mismatch: expected aaa, got bbb")

    class FakeSigningKey:
        @staticmethod
        def from_bytes(_b):
            return object()

    monkeypatch.setattr(tenuo, "Warrant", FakeWarrant)
    monkeypatch.setattr(tenuo, "SigningKey", FakeSigningKey)
    monkeypatch.setattr(
        "tenuo_core.encode_warrant_stack",
        lambda _stack: "stack",
        raising=False,
    )

    with pytest.raises(SystemExit, match="tenuo-admin setup"):
        cli.refresh_subwarrants(cfg)


def test_cmd_check_reports_cloud_binding_failure(monkeypatch, tmp_path, capsys):
    proj = tmp_path / "demo"
    state = proj / ".state"
    state.mkdir(parents=True)
    proj.joinpath("tenuo.yaml").write_text("name: test\nsandbox: ./sandbox\nenforce: {}\n")
    (proj / "sandbox").mkdir()

    monkeypatch.setattr(cli, "DEMO_DIR", proj, raising=False)
    monkeypatch.setattr(cli, "STATE", state, raising=False)
    monkeypatch.setattr(cli, "CONFIG_FILE", proj / "tenuo.yaml", raising=False)
    monkeypatch.setattr(cli, "CLOUD_ENV", state / "cloud.env", raising=False)
    monkeypatch.setattr(cli, "CLOUD_STATE", state / "cloud_state.json", raising=False)
    monkeypatch.setattr(cli, "CLOUD_PROFILE", proj / "tenuo.cloud.yaml", raising=False)
    monkeypatch.setattr(cli, "ADVANCED_PROFILE", proj / "tenuo.advanced.yaml", raising=False)
    monkeypatch.setattr(cli, "ADMIN_ENV", tmp_path / "admin.env", raising=False)
    monkeypatch.setattr(cli, "LAUNCHER", proj / "bin" / "tenuo-claude", raising=False)
    monkeypatch.setattr(cli, "WARRANT", state / "warrant.b64", raising=False)
    monkeypatch.setattr(cli, "STATE_JSON", state / "state.json", raising=False)

    state.joinpath("cloud.env").write_text(
        'export TENUO_CONTROL_PLANE_URL="https://api.example"\n'
        'export TENUO_API_KEY="rt"\n'
    )
    state.joinpath("cloud_state.json").write_text(json.dumps({
        "agent_id": "agt_1",
        "trigger_id": "trig_demo",
    }))

    monkeypatch.setattr(cli, "_docker_ok", lambda: (True, "ok"))
    monkeypatch.setattr(cli, "authorizer_running", lambda _cfg: False)
    monkeypatch.setattr(cli, "check_state_permissions", lambda: (True, []))
    monkeypatch.setattr(cli, "probe_runtime_creds", lambda _c: (True, "ok"))
    monkeypatch.setattr(cli, "read_admin_key", lambda: "admin")
    monkeypatch.setattr(
        cli,
        "probe_cloud_bindings",
        lambda *_a, **_k: (False, "agent allowed_triggers [trig_old] missing trig_demo"),
    )
    monkeypatch.setattr(cli, "intended_mode", lambda _cfg: "cloud")
    monkeypatch.setattr(cli, "cloud_mode_files", lambda: {
        "cloud_env": True, "cloud_state": True, "cloud_profile": False,
    })
    monkeypatch.setattr(cli, "load_config", lambda: {
        "_sandbox_abs": str(proj / "sandbox"),
        "enforce": {},
        "default": "deny",
        "mcp": {},
        "audit": [],
    })

    with pytest.raises(SystemExit) as exc:
        cli.cmd_check(argparse.Namespace())
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "cloud bindings" in out
    assert "trig_old" in out
    assert "tenuo-admin setup" in out
    assert "tenuo-admin setup && tenuo-claude up" in out


def test_cmd_check_cloud_bindings_ok(monkeypatch, tmp_path, capsys):
    proj = tmp_path / "demo"
    state = proj / ".state"
    state.mkdir(parents=True)
    proj.joinpath("tenuo.yaml").write_text(
        "name: test\nsandbox: ./sandbox\nenforce: {}\ndefault: deny\n")
    (proj / "sandbox").mkdir()

    monkeypatch.setattr(cli, "DEMO_DIR", proj, raising=False)
    monkeypatch.setattr(cli, "STATE", state, raising=False)
    monkeypatch.setattr(cli, "CONFIG_FILE", proj / "tenuo.yaml", raising=False)
    monkeypatch.setattr(cli, "CLOUD_ENV", state / "cloud.env", raising=False)
    monkeypatch.setattr(cli, "CLOUD_STATE", state / "cloud_state.json", raising=False)
    monkeypatch.setattr(cli, "CLOUD_PROFILE", proj / "tenuo.cloud.yaml", raising=False)
    monkeypatch.setattr(cli, "ADMIN_ENV", tmp_path / "admin.env", raising=False)
    monkeypatch.setattr(cli, "LAUNCHER", proj / "bin" / "tenuo-claude", raising=False)
    monkeypatch.setattr(cli, "WARRANT", state / "warrant.b64", raising=False)

    state.joinpath("cloud.env").write_text(
        'export TENUO_CONTROL_PLANE_URL="https://api.example"\n'
        'export TENUO_API_KEY="rt"\n'
    )
    state.joinpath("cloud_state.json").write_text(json.dumps({
        "agent_id": "agt_1",
        "trigger_id": "trig_demo",
    }))

    monkeypatch.setattr(cli, "_docker_ok", lambda: (True, "ok"))
    monkeypatch.setattr(cli, "authorizer_running", lambda _cfg: False)
    monkeypatch.setattr(cli, "check_state_permissions", lambda: (True, []))
    monkeypatch.setattr(cli, "probe_runtime_creds", lambda _c: (True, "ok"))
    monkeypatch.setattr(cli, "read_admin_key", lambda: "admin")
    monkeypatch.setattr(
        cli, "probe_cloud_bindings", lambda *_a, **_k: (True, "trigger fire dry-run OK"))
    monkeypatch.setattr(cli, "intended_mode", lambda _cfg: "cloud")
    monkeypatch.setattr(cli, "cloud_mode_files", lambda: {
        "cloud_env": True, "cloud_state": True, "cloud_profile": False,
    })
    monkeypatch.setattr(cli, "load_config", lambda: {
        "_sandbox_abs": str(proj / "sandbox"),
        "enforce": {},
        "default": "deny",
        "mcp": {},
        "audit": [],
    })

    with pytest.raises(SystemExit) as exc:
        cli.cmd_check(argparse.Namespace())
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "cloud bindings" in out
    assert "dry-run OK" in out
