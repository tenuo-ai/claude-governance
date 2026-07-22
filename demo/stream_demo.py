#!/usr/bin/env python3
"""Stage-friendly stream demo for Tenuo + Claude Code.

The script is intentionally a thin wrapper: it drives the real Claude hook,
the real MCP proxy, and `tenuo-claude audit --verify`, then prints large
audience-readable scene output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parent
SANDBOX = ROOT / "sandbox"
POLICY = ROOT / "tenuo.yaml"


def cli_cmd() -> list[str]:
    override = os.environ.get("TENUO_CLAUDE_BIN")
    if override:
        args = os.environ.get("TENUO_CLAUDE_ARGS", "").split()
        return [override, *args]
    found = shutil.which("tenuo-claude")
    if found:
        return [found]
    local = ROOT.parent / ".venv" / "bin" / "python"
    if local.exists():
        return [str(local), "-m", "tenuo_claude_code.cli"]
    return ["tenuo-claude"]


def run_cli(*args: str, input_text: str | None = None, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*cli_cmd(), *args],
        cwd=ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
    )


def set_mode(mode: str) -> None:
    text = POLICY.read_text()
    updated, count = re.subn(r"(?m)^mode:\s*\S+\s*$", f"mode: {mode}", text, count=1)
    if count == 0:
        updated = text.rstrip() + f"\nmode: {mode}\n"
    POLICY.write_text(updated)


def current_mode() -> str:
    data = yaml.safe_load(POLICY.read_text()) or {}
    return str(data.get("mode", "dry-run"))


def banner(title: str, subtitle: str = "") -> None:
    width = 88
    print("\n" + "=" * width)
    print(title.upper().center(width))
    if subtitle:
        print(subtitle.center(width))
    print("=" * width)


def step(num: int, title: str, detail: str) -> None:
    print(f"\n[{num}] {title}")
    print(f"    {detail}")


def audience_text(text: str) -> str:
    text = text.replace(str(ROOT), "$DEMO")
    text = text.replace(str(SANDBOX), "$DEMO/sandbox")
    text = text.replace(
        " (the Bash allowlist authorizes the command *verb*, not file paths \u2014 "
        "add the verb to the shlex list if it's safe; filesystem scope comes "
        "from Read/Write/Edit)",
        "",
    )
    return text


def visible_json(value: dict) -> str:
    return audience_text(json.dumps(value, sort_keys=True))


def print_receipt_tail() -> None:
    result = run_cli("audit", "--tail", "1")
    line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "No receipt written"
    print(f"    AUDIT  {audience_text(line)}")


def hook_call(num: int, title: str, event: dict) -> None:
    step(num, title, f"{event['tool_name']} {visible_json(event.get('tool_input', {}))}")
    result = run_cli("_hook", input_text=json.dumps(event))
    output = result.stdout.strip()
    if output:
        try:
            parsed = json.loads(output)
            decision = parsed["hookSpecificOutput"]["permissionDecision"]
            reason = audience_text(parsed["hookSpecificOutput"]["permissionDecisionReason"])
            print(f"    BLOCK  {decision.upper()} - {reason}")
        except Exception:
            print(f"    HOOK   {output}")
    else:
        print("    HOOK   audit mode: no block returned to Claude")
    print_receipt_tail()


async def mcp_scene(start_num: int) -> None:
    command = cli_cmd()[0]
    args = cli_cmd()[1:] + ["_mcp-proxy"]
    cases = [
        ("read_file", {"path": str(SANDBOX / "notes.txt")},
         "MCP in-scope read", "read incident notes through MCP"),
        ("read_file", {"path": str(ROOT / "fake-secrets.env")},
         "MCP out-of-scope read", "try to read fake secrets through MCP"),
        ("delete_deployment", {"target": "production"},
         "MCP destructive action", "try to delete production through MCP"),
    ]
    env = os.environ.copy()
    env["TENUO_MCP_PROXY_QUIET"] = "1"
    params = StdioServerParameters(command=command, args=args, cwd=str(ROOT), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for offset, (tool, arguments, title, detail) in enumerate(cases):
                step(start_num + offset, title, detail)
                result = await session.call_tool(tool, arguments)
                text = "\n".join(
                    getattr(item, "text", "") for item in result.content
                    if getattr(item, "text", None)
                ).strip()
                first_line = text.splitlines()[0] if text else "<no output>"
                marker = "DENIED" if first_line.startswith("Tenuo denied") else "RESULT"
                print(f"    {marker} {first_line[:150]}")
                print_receipt_tail()


def verify_receipts() -> None:
    banner("Verify Outside The Conversation", "local signatures, hash chain, and warrant replay")
    result = run_cli("audit", "--verify")
    if result.returncode != 0:
        print(result.stdout.rstrip())
        raise SystemExit(result.returncode)
    lines = result.stdout.strip().splitlines()
    print(audience_text(lines[0] if lines else "Receipt verification OK"))
    for line in lines[-6:]:
        if line.startswith("Receipt verification OK"):
            continue
        print(audience_text(line))


async def run_audit() -> None:
    set_mode("dry-run")
    banner("Audit Mode", "same decisions, no blocking yet")
    hook_call(1, "Filesystem secret read", {
        "tool_name": "Read",
        "tool_input": {"file_path": str(ROOT / "fake-secrets.env")},
    })
    hook_call(2, "Off-policy network fetch", {
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://example.com/data"},
    })
    hook_call(3, "Delegated subagent overreach", {
        "tool_name": "Bash",
        "agent_type": "researcher",
        "tool_input": {"command": "ls -la"},
    })
    await mcp_scene(4)
    verify_receipts()


async def run_enforce() -> None:
    set_mode("enforce")
    banner("Enforcement Mode", "same attempts now stop at the boundary")
    hook_call(1, "Filesystem secret read", {
        "tool_name": "Read",
        "tool_input": {"file_path": str(ROOT / "fake-secrets.env")},
    })
    hook_call(2, "Off-policy network fetch", {
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://example.com/data"},
    })
    hook_call(3, "Delegated subagent overreach", {
        "tool_name": "Bash",
        "agent_type": "researcher",
        "tool_input": {"command": "ls -la"},
    })
    await mcp_scene(4)
    verify_receipts()


def preflight() -> None:
    banner("Demo Preflight")
    print(f"policy : {POLICY}")
    print(f"mode   : {current_mode()}")
    result = run_cli("status")
    print(result.stdout.rstrip())
    if result.returncode != 0:
        raise SystemExit(result.returncode)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scene",
        choices=("preflight", "audit", "enforce", "all"),
        nargs="?",
        default="all",
        help="which scene to run",
    )
    args = parser.parse_args()
    if args.scene == "preflight":
        preflight()
    elif args.scene == "audit":
        await run_audit()
    elif args.scene == "enforce":
        await run_enforce()
    else:
        preflight()
        await run_audit()
        await run_enforce()
    print("\nDone.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
