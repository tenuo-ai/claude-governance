#!/usr/bin/env python3
"""Call the demo MCP server through the Tenuo MCP proxy.

This is intentionally small and stage-friendly. It exercises the actual
`tenuo-claude _mcp-proxy` path, so calls produce local MCP proxy receipts.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def _main() -> None:
    root = Path(__file__).resolve().parent
    sandbox = root / "sandbox"
    command = os.environ.get("TENUO_CLAUDE_BIN", "tenuo-claude")
    args = os.environ.get("TENUO_CLAUDE_ARGS", "_mcp-proxy").split()

    cases = [
        ("read_file", {"path": str(sandbox / "notes.txt")},
         "read incident notes through MCP"),
        ("read_file", {"path": str(root / "fake-secrets.env")},
         "read out-of-scope secret through MCP"),
        ("delete_deployment", {"target": "production"},
         "delete production through MCP"),
    ]

    params = StdioServerParameters(command=command, args=args, cwd=str(root))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for tool, arguments, label in cases:
                result = await session.call_tool(tool, arguments)
                text = "\n".join(
                    getattr(item, "text", "") for item in result.content
                    if getattr(item, "text", None)
                ).strip()
                first_line = text.splitlines()[0] if text else "<no output>"
                print(f"{tool:18} {label}")
                print(f"  -> {first_line[:140]}")


if __name__ == "__main__":
    asyncio.run(_main())
