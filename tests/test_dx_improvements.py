"""Unit coverage for the DX-clarity changes.

Pure logic only (no Docker / authorizer / network), in the spirit of conftest:
constraint error messages, the Cloud-drift policy fingerprint, the Bash denial
hint, and the receipt-sink failure signalling. See ROADMAP.md §8.
"""
from __future__ import annotations

import pytest

from tenuo_claude_code import cli


# --- #13: unknown constraint kind lists the valid kinds -----------------------

def test_unknown_constraint_kind_lists_valid_kinds(cli_mod):
    with pytest.raises(SystemExit) as exc:
        cli_mod.make_constraint("nope:bar", "/sb")
    msg = str(exc.value)
    assert "nope" in msg
    # Every supported kind is named so the author doesn't have to grep the docs.
    for kind in ("subpath", "shlex", "regex", "pattern", "oneof", "exact"):
        assert kind in msg


# --- #14: policy fingerprint detects capability drift, ignores local knobs ----

def test_fingerprint_stable_for_identical_policy(cli_mod, make_cfg):
    a = make_cfg(enforce={"Read": "subpath:{sandbox}", "Bash": "shlex:ls,pwd"})
    b = make_cfg(enforce={"Read": "subpath:{sandbox}", "Bash": "shlex:ls,pwd"})
    assert cli_mod.policy_capability_fingerprint(a) == cli_mod.policy_capability_fingerprint(b)


def test_fingerprint_changes_when_capabilities_change(cli_mod, make_cfg):
    base = make_cfg(enforce={"Bash": "shlex:ls,pwd"})
    widened = make_cfg(enforce={"Bash": "shlex:ls,pwd,cat"})  # added a verb
    assert cli_mod.policy_capability_fingerprint(base) != cli_mod.policy_capability_fingerprint(widened)


def test_fingerprint_changes_when_audit_capabilities_change(cli_mod, make_cfg):
    base = make_cfg(audit=["WebSearch"])
    widened = make_cfg(audit=["WebSearch", "TodoWrite"])
    assert cli_mod.policy_capability_fingerprint(base) != cli_mod.policy_capability_fingerprint(widened)


def test_fingerprint_tracks_subagents_and_default(cli_mod, make_cfg):
    base = make_cfg(enforce={"Read": "subpath:{sandbox}"})
    with_role = make_cfg(enforce={"Read": "subpath:{sandbox}"},
                         subagents={"researcher": {"tools": ["Read", "Grep"]}})
    assert cli_mod.policy_capability_fingerprint(base) != cli_mod.policy_capability_fingerprint(with_role)
    assert (cli_mod.policy_capability_fingerprint(make_cfg(default="deny"))
            != cli_mod.policy_capability_fingerprint(make_cfg(default="approve")))


def test_fingerprint_ignores_mode(cli_mod, make_cfg):
    # `mode` is a local runtime posture, NOT baked into the Cloud trigger — it must
    # not register as drift (otherwise flipping enforce<->audit would nag for setup).
    enforce = make_cfg(enforce={"Read": "subpath:{sandbox}"}, mode="enforce")
    audit = make_cfg(enforce={"Read": "subpath:{sandbox}"}, mode="audit")
    assert cli_mod.policy_capability_fingerprint(enforce) == cli_mod.policy_capability_fingerprint(audit)


# --- #15: Bash denials explain the shlex verb-vs-path distinction --------------

def test_bash_denial_reason_augmented(cli_mod, make_cfg):
    cfg = make_cfg(enforce={"Bash": "shlex:ls,pwd"})
    out = cli_mod._augment_denial_reason(cfg, "Bash", "constraint failed")
    assert "constraint failed" in out
    assert "verb" in out.lower() and "path" in out.lower()


def test_non_bash_denial_reason_unchanged(cli_mod, make_cfg):
    cfg = make_cfg(enforce={"Read": "subpath:{sandbox}", "Bash": "shlex:ls"})
    assert cli_mod._augment_denial_reason(cfg, "Read", "nope") == "nope"


def test_bash_without_shlex_not_augmented(cli_mod, make_cfg):
    # If Bash isn't governed by a shlex allowlist, the verb/path note doesn't apply.
    cfg = make_cfg(enforce={"Bash": "regex:^ls$"})
    assert cli_mod._augment_denial_reason(cfg, "Bash", "nope") == "nope"


# --- #17: receipt-write returns success + surfaces a broken sink --------------

def test_write_receipt_returns_true_on_success(cli_mod, bound, monkeypatch):
    monkeypatch.setattr(cli_mod, "RECEIPTS", cli_mod.STATE / "receipts.jsonl", raising=False)
    assert cli_mod.write_receipt({"phase": "pre", "decision": "allow"}) is True
    assert cli_mod.receipt_sink_failure() is None


def test_write_receipt_failure_marks_sink(cli_mod, bound, monkeypatch):
    # Make the append fail without breaking the sibling marker write: point RECEIPTS
    # at a *directory*, so open("a") raises but `.receipt_write_failed` (same parent)
    # is still writable. The call must return False AND record the marker so
    # status/check can surface a broken audit trail.
    cli_mod._receipt_write_warned = False
    receipts = cli_mod.STATE / "receipts.jsonl"
    receipts.mkdir(parents=True)  # a dir where a file is expected
    monkeypatch.setattr(cli_mod, "RECEIPTS", receipts, raising=False)
    assert cli_mod.write_receipt({"phase": "pre", "decision": "allow"}) is False
    assert cli_mod.receipt_sink_failure()  # non-empty marker recorded


def test_write_receipt_clears_marker_on_recovery(cli_mod, bound, monkeypatch):
    # A stale failure marker must be removed once the sink is writable again.
    monkeypatch.setattr(cli_mod, "RECEIPTS", cli_mod.STATE / "receipts.jsonl", raising=False)
    cli_mod.ensure_state_dir()
    cli_mod._receipt_fail_marker().write_text("old failure")
    assert cli_mod.write_receipt({"phase": "pre", "decision": "allow"}) is True
    assert cli_mod.receipt_sink_failure() is None
