# -*- coding: utf-8 -*-
"""Real stdio MCP handshake: call capital_allocation and verify an ImageContent
block actually comes back over the wire, not just from the Python function directly."""
import asyncio
import base64
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
            res = await session.call_tool("capital_allocation", {"symbol": "BALKRISIND"})
            print("blocks:", [type(b).__name__ for b in res.content])
            for b in res.content:
                if type(b).__name__ == "ImageContent":
                    png = base64.b64decode(b.data)
                    with open("scratch_mcp_roundtrip.png", "wb") as f:
                        f.write(png)
                    print("image bytes over the wire:", len(png), "mimeType:", b.mimeType)
                else:
                    print("text len:", len(b.text))


if __name__ == "__main__":
    asyncio.run(main())
