#!/usr/bin/env python3
"""tenuo-demo — scripted allow/deny tour for the reference demo.

Default tour: scope, default-deny, SSRF, subagents, MCP — no WebFetch approval.
Optional `--advanced`: WebFetch approval example when configured in policy.
Optional `--live-approval`: blocks until a Cloud approver responds.

    python3 tenuo_demo.py
    python3 tenuo_demo.py --advanced
    python3 tenuo_demo.py --advanced --live-approval
"""

from __future__ import annotations

import argparse

import tenuo_claude_code.cli as tc
from tenuo_claude_code.paths import bind_project_paths


def _authz(cfg, tool, tin, role=None, *, live=False, skip_approval=False):
    """Run a call through the same decision path the live hook uses."""
    roles = tc.subagent_roles(cfg)
    return tc.authorize_call(
        cfg, tool, tin, role, roles, live=live, skip_approval_gate=skip_approval)


def _tag(allowed: bool, reason: str) -> str:
    if allowed:
        return "ALLOW"
    return "PAUSE" if reason.startswith(tc.APPROVAL_PENDING_REASON) else "DENY "


def run_default_tour(cfg) -> None:
    sb = cfg["_sandbox_abs"]
    print("Tenuo + Claude Code — enforcement demo\n" + "=" * 40)
    cases = [
        ("Read", {"file_path": f"{sb}/incident-report.md"}, "summarize an in-scope file"),
        ("Read", {"file_path": f"{sb}/../fake-secrets.env"}, "read out-of-scope credentials file"),
        ("Bash", {"command": "ls -la"}, "inert command"),
        ("Bash", {"command": "ls && rm -rf /"}, "command chaining (Shlex blocks)"),
        ("Grep", {"pattern": "TODO", "path": sb}, "in-scope search"),
        ("Grep", {"pattern": "secret", "path": "/etc"}, "out-of-scope search"),
        ("WebFetch", {"url": "https://api.github.com/repos"}, "allowlisted domain"),
        ("WebFetch", {"url": "https://example.com/data"}, "off-allowlist (denied by domain allowlist)"),
        ("WebFetch", {"url": "http://169.254.169.254/latest/meta-data/"}, "cloud metadata (SSRF)"),
        ("WebFetch", {"url": "http://2130706433/"}, "decimal-encoded loopback (SSRF)"),
        ("NotebookEdit", {"notebook_path": "x.ipynb"}, "un-listed tool (default-deny)"),
    ]
    for tool, tin, label in cases:
        # Default tour always uses strict allowlist deny, even if tenuo.advanced.yaml exists.
        allowed, reason, gov, _ = _authz(cfg, tool, tin, skip_approval=True)
        tag = _tag(allowed, reason)
        extra = "" if allowed else f"({reason})"
        scope = "enforced" if gov else ("audit" if tool in tc.audit_map(cfg) else "default")
        print(f"  {tag} {tool:17} [{scope:8}] {label} {extra}")
    print("\nScope test: secret reads and destructive MCP calls stay denied "
          "whether or not the model follows instructions in file content.")

    mcp_enforce = (cfg.get("mcp") or {}).get("enforce") or {}
    if (cfg.get("mcp") or {}).get("downstream") and mcp_enforce:
        print("\nMCP (proxy + mcp.enforce; unlisted tools default-deny)")
        print("-" * 40)
        mcp_cases = [
            ("read_file", {"path": f"{sb}/notes.txt"}, "read in sandbox via MCP"),
            ("read_file", {"path": "/etc/passwd"}, "read outside sandbox via MCP"),
        ]
        dd_raw = mcp_enforce.get("delete_deployment")
        if dd_raw is not None:
            parsed = tc.parse_mcp_enforce_spec(dd_raw)
            if parsed.get("approval"):
                mcp_cases.extend([
                    ("delete_deployment", {"target": "staging"}, "exempt target (direct allow when wired)"),
                    ("delete_deployment", {"target": "production"}, "approval-gated target"),
                ])
            else:
                mcp_cases.append(
                    ("delete_deployment", {"target": "production"}, "governed MCP tool"))
        else:
            mcp_cases.append(
                ("delete_deployment", {"target": "production"}, "unlisted tool (default-deny)"))
        for tool, tin, label in mcp_cases:
            allowed, reason, gov, _ = _authz(cfg, tool, tin, skip_approval=True)
            tag = _tag(allowed, reason)
            extra = "" if allowed else f"({reason})"
            scope = "enforced" if gov else "default"
            print(f"  {tag} {tool:17} [{scope:8}] {label} {extra}")

    if tc.subagent_roles(cfg):
        print("\nIncident delegation (Claude Code `Agent` tool) — spawn gate + child warrant")
        print("-" * 40)
        sub_cases = [
            ("Agent", {"subagent_type": "researcher"}, None, "delegate evidence review to the researcher"),
            ("Agent", {"subagent_type": "deployer"}, None, "try to spawn an undeclared responder"),
            ("Read", {"file_path": f"{sb}/incident-report.md"}, "researcher", "read the incident report"),
            ("Grep", {"pattern": "checkout-api", "path": sb}, "researcher", "search local evidence"),
            ("Bash", {"command": "ls -la"}, "researcher", "run a command the parent session allows"),
            ("WebFetch", {"url": "https://api.github.com/repos"}, "researcher", "fetch a domain the parent session allows"),
        ]
        for tool, tin, role, label in sub_cases:
            allowed, reason, _, _ = _authz(cfg, tool, tin, role, skip_approval=True)
            tag = "ALLOW" if allowed else "DENY "
            extra = "" if allowed else f"({reason})"
            print(f"  {tag} {tool:9} as {role or 'main':11} {label} {extra}")
        print("\nThe parent session has Bash and WebFetch, but the researcher only receives "
              "read/search authority. The model can ask for more; the child warrant cannot grow.")
    else:
        print("\nSubagents — skipped (`subagents:` not declared; flat session warrant).")


def run_advanced_tour(cfg, live: bool) -> None:
    if not tc.has_approval_gates(cfg):
        print("\nHuman approval — not configured.")
        print("  See README § Human approval and tenuo.yaml.advanced.example.")
        return

    gates = tc.approval_entries(cfg)
    print("\nHuman approval — same Cloud workflow for native hook and MCP proxy")
    print("-" * 40)
    for cap, _ in gates:
        print(f"  gated: {cap}")

    if tc.webfetch_approval(cfg):
        print("\n  Native hook — WebFetch (off-allowlist SSRF-safe URL)")
        url = "https://example.com/data"
        allowed, reason, _, _ = _authz(cfg, "WebFetch", {"url": url}, skip_approval=False)
        tag = _tag(allowed, reason)
        extra = "" if allowed else f"({reason})"
        print(f"    {tag} WebFetch  [enforced] off-allowlist URL {extra}")
        print("    Allowlisted domains pass; SSRF URLs hard-denied.")

        if live:
            live_url = "https://example.com/off-allowlist-test"
            print(f"\n    LIVE WebFetch {live_url}")
            print("    waiting for approver…")
            allowed, reason, _, _ = _authz(
                cfg, "WebFetch", {"url": live_url}, live=True, skip_approval=False)
            print(f"    -> {'ALLOWED' if allowed else 'BLOCKED'}: {reason}")

    mcp_gated = {
        tool: parsed for tool, parsed in tc.mcp_enforce_entries(cfg).items()
        if parsed.get("approval")}
    if "delete_deployment" in mcp_gated:
        print("\n  MCP proxy — delete_deployment (target argument)")
        allowed, reason, _, _ = _authz(
            cfg, "delete_deployment", {"target": "staging"}, skip_approval=False)
        tag = _tag(allowed, reason)
        extra = "" if allowed else f"({reason})"
        print(f"    {tag} delete_deployment  [enforced] target=staging (exempt) {extra}")
        allowed, reason, _, _ = _authz(
            cfg, "delete_deployment", {"target": "production"}, skip_approval=False)
        tag = _tag(allowed, reason)
        extra = "" if allowed else f"({reason})"
        print(f"    {tag} delete_deployment  [enforced] target=production (gated) {extra}")

        if live:
            print("\n    LIVE delete_deployment target=production")
            print("    waiting for approver…")
            allowed, reason, _, _ = _authz(
                cfg, "delete_deployment", {"target": "production"},
                live=True, skip_approval=False)
            print(f"    -> {'ALLOWED' if allowed else 'BLOCKED'}: {reason}")

    if not live:
        print("\nRun with --live-approval to block until an approver responds.")


def run_demo(*, advanced: bool, live_approval: bool) -> None:
    bind_project_paths(tc)
    if not tc._status_json():
        raise SystemExit("Authorizer not running. Run `tenuo-claude up` first.")
    cfg = tc.load_config()
    run_default_tour(cfg)
    if advanced:
        run_advanced_tour(cfg, live=live_approval)
    elif tc.has_approval_gates(cfg):
        print("\n(Human approval is configured; run with --advanced for WebFetch + MCP examples.)")
        print("  Run: tenuo-claude demo --advanced")
    print("\nEach line above uses the same authorizer decision path as the Claude hook "
          "and MCP proxy. Real hook/proxy calls also write signed local receipts; "
          "run `tenuo-claude audit --verify` after a Claude Code run.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tenuo-demo", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--advanced", action="store_true",
                        help="include human approval scenarios (WebFetch + MCP; requires overlay in policy)")
    parser.add_argument("--live-approval", action="store_true",
                        help="with --advanced: block until approver responds (Cloud)")
    args = parser.parse_args()
    if args.live_approval and not args.advanced:
        raise SystemExit("--live-approval requires --advanced")
    bind_project_paths(tc)
    tc.assert_no_admin_key()
    run_demo(advanced=args.advanced, live_approval=args.live_approval)


if __name__ == "__main__":
    main()
