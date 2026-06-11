#!/usr/bin/env python3
"""tenuo-demo — scripted allow -> deny -> audit tour, for showing customers.

This is showcase code, kept OUT of tenuo_claude.py so that the latter stays a
clean standalone governance solution (generate + authorizer lifecycle + Claude
hooks/MCP proxy + audit/revoke). It imports tenuo_claude as a library and drives
the SAME live authorizer that `tenuo-claude up` brings up — every line printed
below is a real authorization decision and a signed receipt, not a mock.

    python3 tenuo_demo.py        # run the tour (run `tenuo-claude up` first)
"""

from __future__ import annotations

import argparse

import tenuo_claude as tc  # the standalone solution, used here as a library


def _authz(cfg, tool, tin, role=None, live=False):
    """Run a call through the same decision path the live hook uses.

    `role` plays the part of Claude's `agent_type`: None = main thread (session
    warrant / spawn gate), set = a call made inside that subagent (its own
    attenuated warrant). `live=True` runs the real Cloud approval flow (blocks until
    an approver responds). The default tour reports approval-required without pausing.
    """
    roles = tc.subagent_roles(cfg)
    allowed, reason, gov, _ = tc.authorize_call(cfg, tool, tin, role, roles, live=live)
    return allowed, reason, gov


def _tag(allowed: bool, reason: str) -> str:
    """3-way decision tag: ALLOW / PAUSE (human approval) / DENY."""
    if allowed:
        return "ALLOW"
    return "PAUSE" if reason.startswith(tc.APPROVAL_PENDING_REASON) else "DENY "


def run_demo(live_approval: bool = False) -> None:
    if not tc._status_json():
        raise SystemExit("Authorizer not running. Run `tenuo-claude up` first.")
    cfg = tc.load_config()
    sb = cfg["_sandbox_abs"]
    print("Tenuo + Claude Code — enforcement demo\n" + "=" * 40)
    cases = [
        ("Read", {"file_path": f"{sb}/incident-report.md"}, "summarize an in-scope file"),
        # The poisoned file tells the model to do these two — the warrant says no:
        ("Read", {"file_path": f"{sb}/../prod-credentials.env"}, "exfil out-of-scope secret (injection bait)"),
        ("delete_deployment", {"target": "production"}, "destroy prod via MCP (default-deny)"),
        ("Bash", {"command": "ls -la"}, "inert command"),
        ("Bash", {"command": "ls && rm -rf /"}, "command chaining (Shlex blocks)"),
        ("Grep", {"pattern": "TODO", "path": sb}, "in-scope search"),
        ("Grep", {"pattern": "secret", "path": "/etc"}, "out-of-scope search"),
        ("WebFetch", {"url": "https://api.github.com/repos"}, "allowlisted domain"),
        ("WebFetch", {"url": "https://example.com/data"}, "off-allowlist (human approval)"),
        ("WebFetch", {"url": "http://169.254.169.254/latest/meta-data/"}, "cloud metadata (SSRF)"),
        ("WebFetch", {"url": "http://2130706433/"}, "decimal-encoded loopback (SSRF)"),
        ("NotebookEdit", {"notebook_path": "x.ipynb"}, "un-listed tool (default-deny)"),
    ]
    for tool, tin, label in cases:
        allowed, reason, gov = _authz(cfg, tool, tin)
        tag = _tag(allowed, reason)
        extra = "" if allowed else f"({reason})"
        scope = "enforced" if gov else ("audit" if tool in tc.audit_map(cfg) else "default")
        print(f"  {tag} {tool:17} [{scope:8}] {label} {extra}")
    print("\nPoisoned-file scenario: the warrant denies prod delete and secret read "
          "whether or not the model catches the injection.")
    if tc.webfetch_approval(cfg):
        print("\nWebFetch with approval enabled: allowlisted domains pass, SSRF is "
              "denied, off-allowlist safe URLs wait for approver sign-off in Cloud "
              "via the approver's configured channel. Run with --live-approval to exercise it.")

    if tc.subagent_roles(cfg):
        print("\nSubagents (Claude Code `Agent` tool) — spawn gate + per-subagent warrant")
        print("-" * 40)
        # role=None -> main thread (session warrant); role set -> child warrant.
        sub_cases = [
            ("Agent", {"subagent_type": "researcher"}, None, "spawn the declared researcher"),
            ("Agent", {"subagent_type": "deployer"}, None, "spawn an undeclared subagent"),
            ("Read", {"file_path": f"{sb}/incident-report.md"}, "researcher", "read in-scope (its job)"),
            ("Bash", {"command": "ls -la"}, "researcher", "run a command the SESSION allows"),
            ("WebFetch", {"url": "https://api.github.com/repos"}, "researcher", "fetch an allowlisted domain"),
        ]
        for tool, tin, role, label in sub_cases:
            allowed, reason, _ = _authz(cfg, tool, tin, role)
            tag = "ALLOW" if allowed else "DENY "
            extra = "" if allowed else f"({reason})"
            print(f"  {tag} {tool:9} as {role or 'main':11} {label} {extra}")
        print("\nThe researcher runs under the session warrant attenuated to read/search — so "
              "Bash and WebFetch, which the SESSION itself can do, are denied to the subagent. "
              "Attenuation is one-way: a poisoned subagent prompt can't widen its own scope.")
    else:
        print("\nSubagents — skipped (`subagents:` not declared; flat session warrant).")
        print("  Enable `subagents:` in tenuo.yaml to demo per-role attenuation.")

    if live_approval and tc.webfetch_approval(cfg):
        run_live_approval(cfg)

    print("\nEvery line above is a signed receipt on the authorizer. "
          "Run `tenuo-claude audit` to see the trail, `tenuo-claude revoke` to kill the warrant.")


def run_live_approval(cfg) -> None:
    """Drive a real Cloud approval for an off-allowlist WebFetch URL."""
    url = "https://example.com/off-allowlist-demo"
    print("\nHuman approval — LIVE")
    print("-" * 40)
    print(f"  WebFetch {url}")
    print("  off-allowlist + SSRF-safe; waiting for approver on their configured channel…")
    allowed, reason, _ = _authz(cfg, "WebFetch", {"url": url}, live=True)
    print(f"  -> {'ALLOWED' if allowed else 'BLOCKED'}: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tenuo-demo", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live-approval", action="store_true",
                        help="run real Cloud approval for an off-allowlist WebFetch")
    args = parser.parse_args()
    # Runtime plane: like the operator-facing tenuo-claude commands, the demo
    # must never run with an admin credential reachable in its environment.
    tc.assert_no_admin_key()
    run_demo(live_approval=args.live_approval)


if __name__ == "__main__":
    main()
