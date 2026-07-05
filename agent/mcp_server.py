"""Standalone stdio MCP server exposing the Finance data-lake tools.

This lets the Finance agent run NATIVELY inside a Claude harness (Claude Desktop /
Claude Code) using the harness's own authentication — no API key, no nested SDK
auth. Register it in .mcp.json; the harness spawns this over stdio and gets all
findata tools (see ALL_TOOLS in tools.py for the current count/list). The
procedural workflows live in .claude/skills/, and the agent guidance lives in
CLAUDE.md, so tools + skills + instructions all load natively.

Run standalone (for a smoke test):  python -m agent.mcp_server  (then it waits on stdio)
"""
from __future__ import annotations

import asyncio

import mcp.types as mtypes
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .tools import ALL_TOOLS

_BY_NAME = {t.name: t for t in ALL_TOOLS}

server = Server("findata")


@server.list_tools()
async def list_tools() -> list[mtypes.Tool]:
    return [
        mtypes.Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
        for t in ALL_TOOLS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[mtypes.TextContent | mtypes.ImageContent]:
    tool = _BY_NAME.get(name)
    if tool is None:
        return [mtypes.TextContent(type="text", text=f"ERROR: unknown tool '{name}'")]
    result = await tool.handler(arguments or {})
    blocks = result.get("content", [])
    out: list[mtypes.TextContent | mtypes.ImageContent] = []
    for b in blocks:
        if b.get("type") == "text":
            out.append(mtypes.TextContent(type="text", text=b.get("text", "")))
        elif b.get("type") == "image":
            out.append(mtypes.ImageContent(type="image", data=b["data"],
                                           mimeType=b.get("mimeType", "image/png")))
    return out or [mtypes.TextContent(type="text", text="(no output)")]


async def _main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
