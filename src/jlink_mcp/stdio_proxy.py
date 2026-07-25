"""Transparent MCP stdio-to-Streamable-HTTP bridge."""

from __future__ import annotations

import anyio
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.stdio import stdio_server


async def _relay(source: anyio.abc.ObjectReceiveStream, destination: anyio.abc.ObjectSendStream) -> None:
    async with source, destination:
        async for message in source:
            if isinstance(message, Exception):
                raise message
            await destination.send(message)


async def run_stdio_proxy(url: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with stdio_server() as (stdio_read, stdio_write):
        async with streamablehttp_client(url, headers=headers) as (
            http_read,
            http_write,
            _,
        ):
            async with anyio.create_task_group() as group:
                group.start_soon(_relay, stdio_read, http_write)
                group.start_soon(_relay, http_read, stdio_write)
