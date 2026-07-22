# -*- coding: utf-8 -*-
"""Spawn agent.mcp_server over stdio and do a real MCP handshake: list tools + call one."""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agent.mcp_server"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"tools listed: {len(tools)}")
            print("names:", ", ".join(t.name for t in tools))
            # call one non-RAG tool end to end
            res = await session.call_tool("financial_health", {"symbol": "BALKRISIND"})
            txt = res.content[0].text
            print("\ncall financial_health(BALKRISIND) ->", txt[:200].replace("\n", " "))


if __name__ == "__main__":
    asyncio.run(main())
