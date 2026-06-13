"""Tests for Cloud admin setup helpers."""

from __future__ import annotations

import pytest

from tenuo_claude_code import admin


def test_resolve_approver_identity_by_id(monkeypatch):
    def fake_cloud_api(method, url, api_key, path, body=None):
        assert (method, path) == ("GET", "/v1/identities")
        return 200, {
            "identities": [
                {"id": "idn_1", "display_name": "Alice Example", "public_key": "pub1"},
                {"id": "idn_2", "display_name": "Alice Example", "public_key": "pub2"},
            ],
        }

    monkeypatch.setattr(admin.tc, "cloud_api", fake_cloud_api)

    assert admin.resolve_approver_identity(
        "https://api.example", "admin", "idn_2", by_id=True
    ) == ("idn_2", "Alice Example", "pub2")


def test_resolve_approver_identity_by_display_name(monkeypatch):
    def fake_cloud_api(method, url, api_key, path, body=None):
        assert (method, path) == ("GET", "/v1/identities")
        return 200, {
            "identities": [
                {"id": "idn_1", "display_name": "Alice Example", "public_key": "pub1"},
            ],
        }

    monkeypatch.setattr(admin.tc, "cloud_api", fake_cloud_api)

    assert admin.resolve_approver_identity(
        "https://api.example", "admin", "Alice Example"
    ) == ("idn_1", "Alice Example", "pub1")


def test_resolve_approver_identity_rejects_duplicate_display_name(monkeypatch):
    def fake_cloud_api(method, url, api_key, path, body=None):
        assert (method, path) == ("GET", "/v1/identities")
        return 200, {
            "identities": [
                {"id": "idn_1", "display_name": "Alice Example", "public_key": "pub1"},
                {"id": "idn_2", "display_name": "Alice Example", "public_key": "pub2"},
            ],
        }

    monkeypatch.setattr(admin.tc, "cloud_api", fake_cloud_api)

    with pytest.raises(SystemExit, match="approver_identity_id"):
        admin.resolve_approver_identity("https://api.example", "admin", "Alice Example")


def test_ensure_agent_trigger_binding_patches_stale_allowed_trigger(monkeypatch):
    calls = []

    def fake_cloud_api(method, url, api_key, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return 200, {"allowed_triggers": ["trig_old"]}
        if method == "PATCH":
            return 200, {"ok": True}
        raise AssertionError(method)

    monkeypatch.setattr(admin.tc, "cloud_api", fake_cloud_api)

    changed = admin.ensure_agent_trigger_binding("https://api.example", "admin", "agt_1", "trig_new")

    assert changed is True
    assert calls == [
        ("GET", "/v1/agents/agt_1", None),
        ("PATCH", "/v1/agents/agt_1", {"allowed_triggers": ["trig_new"]}),
    ]


def test_ensure_agent_trigger_binding_keeps_current_allowed_trigger(monkeypatch):
    calls = []

    def fake_cloud_api(method, url, api_key, path, body=None):
        calls.append((method, path, body))
        return 200, {"allowed_triggers": ["trig_new"]}

    monkeypatch.setattr(admin.tc, "cloud_api", fake_cloud_api)

    changed = admin.ensure_agent_trigger_binding("https://api.example", "admin", "agt_1", "trig_new")

    assert changed is False
    assert calls == [("GET", "/v1/agents/agt_1", None)]


def test_ensure_agent_trigger_binding_fails_on_patch_error(monkeypatch):
    def fake_cloud_api(method, url, api_key, path, body=None):
        if method == "GET":
            return 200, {"allowed_triggers": ["trig_old"]}
        return 403, {"error": {"code": "forbidden"}}

    monkeypatch.setattr(admin.tc, "cloud_api", fake_cloud_api)

    with pytest.raises(SystemExit, match="Reconcile agent trigger binding failed"):
        admin.ensure_agent_trigger_binding("https://api.example", "admin", "agt_1", "trig_new")


def test_build_warrant_config_mcp_approval_gate():
    cfg = {
        "_sandbox_abs": "/sandbox",
        "enforce": {
            "WebFetch": {
                "domains": ["api.github.com"],
                "approval": {"threshold": 1},
            },
        },
        "mcp": {
            "enforce": {
                "read_file": "subpath:{sandbox}",
                "delete_deployment": {
                    "approval": {
                        "threshold": 1,
                        "exempt": {"target": "exact:staging"},
                    },
                },
            },
        },
        "default": "deny",
        "audit": [],
    }
    wc = admin.build_warrant_config(cfg, "apol_test")

    assert wc["per_action_constraints"]["delete_deployment"] == {
        "target": {"_type": "wildcard"},
    }
    assert wc["per_action_constraints"]["read_file"] == {
        "path": {"_type": "subpath", "_value": "/sandbox"},
    }
    gates = wc["approval_gates"]
    assert gates["_policy_id"] == "apol_test"
    assert gates["delete_deployment"]["args"]["target"]["exempt"] == {
        "_type": "exact",
        "_value": "staging",
    }
    assert "web_fetch" in gates
    assert gates["web_fetch"]["args"]["host"]["exempt"]["_type"] == "regex"


def test_build_warrant_config_mcp_approval_without_policy_skips_gate():
    cfg = {
        "_sandbox_abs": "/sandbox",
        "enforce": {},
        "mcp": {
            "enforce": {
                "delete_deployment": {
                    "approval": {"threshold": 1, "exempt": {"target": "exact:staging"}},
                },
            },
        },
        "default": "deny",
        "audit": [],
    }
    wc = admin.build_warrant_config(cfg, None)

    assert "delete_deployment" not in wc["per_action_constraints"]
    assert "approval_gates" not in wc
