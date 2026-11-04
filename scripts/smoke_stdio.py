# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Start the server over stdio, list its tools and read the usage guide.

No connection to OpenText is made, so this only proves the process starts, speaks
MCP 2026-07-28 and exposes the expected tools. Run it from the project directory
(a ``.env`` file there is picked up) or with OTCS_* variables in the environment:

    python scripts/smoke_stdio.py
"""

import asyncio
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


async def main() -> int:
    """Return 0 when the server answers as expected."""
    environment = {**os.environ, "MCP_TRANSPORT": "stdio"}
    transport = StdioTransport(
        command=sys.executable, args=["-m", "otcs_mcp"], env=environment, keep_alive=False
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        guide = await client.read_resource("otcs-mcp://usage-guide")
        protocol = client.protocol_version
    names = ", ".join(tool.name for tool in tools)
    print(f"OK: protocol {protocol}, {len(tools)} tools: {names}")
    print(f"OK: usage guide has {len(guide[0].text)} characters")
    return 0 if tools else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
