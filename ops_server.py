#!/usr/bin/env python3
"""
Demo "ops" MCP server for the Tenuo + Claude Code governance demo.

Self-contained on purpose: this server belongs to the demo, not to the SDK
examples, so the whole demo lives under one directory. It exposes two inert
filesystem tools plus one deliberately destructive ops tool
(`delete_deployment`) used to give a denied tool call real stakes:

    "The agent tried to delete prod and the warrant said no."

`delete_deployment` is SIMULATED — it never touches anything. In the demo it is
intentionally NOT listed in `tenuo.yaml`'s `mcp.enforce`, so the Tenuo MCP proxy
default-denies it before it ever reaches this code.
"""

import asyncio
import sys
from pathlib import Path

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    print("MCP SDK not installed. Install with: uv pip install mcp", file=sys.stderr)
    sys.exit(1)


server = Server("tenuo-demo-ops-server")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description="Read contents of a file",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "max_size": {"type": "integer", "description": "Maximum bytes to read", "default": 1048576},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="list_directory",
            description="List files in a directory",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path"}},
                "required": ["path"],
            },
        ),
        Tool(
            name="delete_deployment",
            description="Tear down a deployment and all of its running services (irreversible)",
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Deployment/environment to delete, e.g. 'production'"},
                },
                "required": ["target"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "read_file":
        path = arguments["path"]
        max_size = arguments.get("max_size", 1048576)
        try:
            file_path = Path(path)
            if not file_path.exists():
                return [TextContent(type="text", text=f"Error: File not found: {path}")]
            with open(file_path, "r") as f:
                content = f.read(max_size)
            return [TextContent(type="text", text=content)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error reading file: {e}")]

    elif name == "list_directory":
        path = arguments["path"]
        try:
            dir_path = Path(path)
            if not dir_path.is_dir():
                return [TextContent(type="text", text=f"Error: Not a directory: {path}")]
            files = [f.name for f in dir_path.iterdir()]
            return [TextContent(type="text", text="\n".join(files))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error listing directory: {e}")]

    elif name == "delete_deployment":
        # SIMULATED. A real implementation would irreversibly tear down the
        # target environment. In this demo the call is denied by the Tenuo MCP
        # proxy before it reaches here, so it stays a no-op.
        target = arguments.get("target", "<unspecified>")
        return [TextContent(
            type="text",
            text=(f"[SIMULATED] Would tear down deployment '{target}' and stop all "
                  f"of its services. (No action taken — this is a demo tool.)"),
        )]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
