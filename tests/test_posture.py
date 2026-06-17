"""Posture model: mode (enforce/dry-run), default (deny/approve), allow-list.

Covers the redesigned vocabulary and its deprecated aliases (mode: audit,
default: audit|allow, audit:/audit_extra:/audit_bundled:). Pure logic + a couple
of load_config round-trips with a temp policy file; no Docker/authorizer/network.
See writing/hitl-mcp-gateway-plan.md and the posture redesign.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import tenuo_claude_code
from tenuo_claude_code import cli

_BUNDLED_HARNESS = Path(tenuo_claude_code.__file__).parent / "data" / "harness_tools.yaml"


# --- mode: enforce / dry-run (+ audit alias) --------------------------------

@pytest.mark.parametrize("value,dry_run", [
    (None, False), ("enforce", False),
    ("dry-run", True), ("dry_run", True), ("dryrun", True),
    ("audit", True),            # deprecated alias
    ("AUDIT", True),
])
def test_is_dry_run_mode(cli_mod, make_cfg, value, dry_run):
    cfg = make_cfg(mode=value) if value is not None else make_cfg()
    assert cli_mod.is_dry_run_mode(cfg) is dry_run


# --- default: approve pulls the catch-all into the approval set -------------

def test_approval_entries_includes_catchall_on_approve(cli_mod, make_cfg):
    assert not cli_mod.approval_entries(make_cfg(default="deny"))
    entries = cli_mod.approval_entries(make_cfg(default="approve"))
    caps = [c for c, _ in entries]
    assert cli_mod.CATCHALL_AUDIT in caps
    assert cli_mod.has_approval_gates(make_cfg(default="approve"))


# --- advisories: deprecations + local approve degradation -------------------

def test_posture_advisories_passes_through_deprecations(cli_mod, make_cfg):
    cfg = make_cfg()
    cfg["_deprecations"] = ["`mode: audit` is deprecated — rename to `mode: dry-run`"]
    assert "mode: audit" in " ".join(cli_mod.posture_advisories(cfg))


def test_posture_advisories_flags_local_approve(cli_mod, make_cfg, bound):
    # No Cloud creds (bound points cloud.env at a nonexistent temp path) -> approve
    # can't be honored -> advisory.
    notes = cli_mod.posture_advisories(make_cfg(default="approve"))
    assert any("requires Tenuo Cloud" in n for n in notes)


# --- _normalize_posture_keys: canonicalize + record deprecations ------------

def test_normalize_rejects_unknown_mode(cli_mod):
    with pytest.raises(SystemExit):
        cli_mod._normalize_posture_keys({"mode": "bogus"})


def test_normalize_rejects_unknown_default(cli_mod):
    with pytest.raises(SystemExit):
        cli_mod._normalize_posture_keys({"default": "bogus"})


@pytest.mark.parametrize("legacy", ["audit", "allow"])
def test_normalize_collapses_legacy_default_to_deny(cli_mod, legacy):
    cfg = {"default": legacy}
    deps = cli_mod._normalize_posture_keys(cfg)
    assert cfg["default"] == "deny"
    assert any("no longer supported" in d for d in deps)


def test_normalize_flags_deprecated_mode_audit(cli_mod):
    deps = cli_mod._normalize_posture_keys({"mode": "audit"})
    assert any("mode: audit" in d for d in deps)


def test_normalize_clean_policy_has_no_deprecations(cli_mod):
    assert cli_mod._normalize_posture_keys({"mode": "enforce", "default": "approve"}) == []


# --- load_config: allow / allow_bundled + legacy audit* aliases -------------

def _write_policy(monkeypatch, tmp_path, body: str):
    cfg_file = tmp_path / "tenuo.yaml"
    cfg_file.write_text(body)
    monkeypatch.setattr(cli, "CONFIG_FILE", cfg_file, raising=False)
    monkeypatch.setattr(cli, "DEMO_DIR", tmp_path, raising=False)
    monkeypatch.setattr(cli, "CLOUD_PROFILE", tmp_path / "nope-cloud.yaml", raising=False)
    monkeypatch.setattr(cli, "ADVANCED_PROFILE", tmp_path / "nope-adv.yaml", raising=False)
    monkeypatch.setattr(cli, "HARNESS_TOOLS_FILE", _BUNDLED_HARNESS, raising=False)


def test_load_config_allow_list_merges_with_bundled(cli_mod, tmp_path, monkeypatch):
    _write_policy(monkeypatch, tmp_path, "name: t\nallow:\n  - CustomReadOnly\n")
    cfg = cli_mod.load_config()
    assert "CustomReadOnly" in cfg["audit"]      # user allow entry present
    assert "TodoWrite" in cfg["audit"]           # bundled inert tool merged
    assert cfg["_deprecations"] == []


def test_load_config_allow_bundled_false_drops_bundled(cli_mod, tmp_path, monkeypatch):
    _write_policy(monkeypatch, tmp_path, "name: t\nallow_bundled: false\nallow:\n  - OnlyThis\n")
    cfg = cli_mod.load_config()
    assert cfg["audit"] == ["OnlyThis"]


def test_load_config_legacy_audit_keys_alias_with_deprecation(cli_mod, tmp_path, monkeypatch):
    _write_policy(monkeypatch, tmp_path,
                  "name: t\naudit:\n  - LegacyTool\naudit_extra:\n  - ExtraTool\naudit_bundled: false\n")
    cfg = cli_mod.load_config()
    assert set(cfg["audit"]) == {"LegacyTool", "ExtraTool"}   # bundled disabled
    joined = " ".join(cfg["_deprecations"])
    assert "`audit:`" in joined and "audit_extra" in joined and "audit_bundled" in joined


def test_load_config_legacy_default_audit_becomes_deny(cli_mod, tmp_path, monkeypatch):
    _write_policy(monkeypatch, tmp_path, "name: t\ndefault: audit\n")
    cfg = cli_mod.load_config()
    assert cfg["default"] == "deny"
    assert cli_mod.default_mode(cfg) == "deny"
    assert any("no longer supported" in d for d in cfg["_deprecations"])


# --- default: approve fails CLOSED (never fail-open) -------------------------

def test_approve_fails_closed_on_plain_allow(cli_mod, make_cfg, monkeypatch):
    # If the authorizer returns a plain allow for the catch-all WITHOUT an approval
    # (e.g. a gate that no-ops at enforcement), the runtime must deny it.
    cfg = make_cfg(default="approve")
    monkeypatch.setattr(cli_mod, "authorize_with_approval", lambda *a, **k: (True, "authorized"))
    allowed, reason, _, cap = cli_mod.authorize_call(cfg, "SomeUnlistedTool", {}, None, {}, live=True)
    assert cap == cli_mod.CATCHALL_AUDIT
    assert allowed is False
    assert "failing closed" in reason


def test_approve_allows_only_when_actually_approved(cli_mod, make_cfg, monkeypatch):
    cfg = make_cfg(default="approve")
    monkeypatch.setattr(cli_mod, "authorize_with_approval", lambda *a, **k: (True, "approved"))
    allowed, _, _, cap = cli_mod.authorize_call(cfg, "SomeUnlistedTool", {}, None, {}, live=True)
    assert cap == cli_mod.CATCHALL_AUDIT
    assert allowed is True


def test_approve_safety_net_scoped_to_catchall(cli_mod, make_cfg, monkeypatch):
    # The net must NOT fire for an enforced/governed tool — only the catch-all.
    cfg = make_cfg(default="approve", enforce={"Read": "subpath:{sandbox}"})
    monkeypatch.setattr(cli_mod, "authorize_with_approval", lambda *a, **k: (True, "authorized"))
    allowed, _, _, cap = cli_mod.authorize_call(
        cfg, "Read", {"file_path": "/sandbox/x"}, None, {}, live=True)
    assert cap != cli_mod.CATCHALL_AUDIT
    assert allowed is True


def test_deny_default_not_affected_by_safety_net(cli_mod, make_cfg, monkeypatch):
    cfg = make_cfg(default="deny")
    monkeypatch.setattr(cli_mod, "authorize_with_approval", lambda *a, **k: (True, "authorized"))
    allowed, _, _, cap = cli_mod.authorize_call(cfg, "SomeUnlistedTool", {}, None, {}, live=True)
    assert cap == cli_mod.CATCHALL_DENY     # ungranted catch-all under deny
    assert allowed is True                  # net only applies to approve
