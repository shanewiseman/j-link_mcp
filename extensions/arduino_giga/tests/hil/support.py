from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


HIL_ENABLED = os.environ.get("JLINK_MCP_HIL") == "1"
GUI_ENABLED = os.environ.get("JLINK_MCP_GUI") == "1"


def unpack(result):
    assert not result.isError, result.content
    if result.structuredContent is not None:
        value = result.structuredContent
        return value.get("result", value) if isinstance(value, dict) else value
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError("MCP result had no structured or JSON text content")


@asynccontextmanager
async def session():
    url = os.environ.get("JLINK_MCP_URL", "http://127.0.0.1:8000/mcp")
    token = os.environ.get("JLINK_MCP_TOKEN")
    if not token:
        token_file = os.environ.get("JLINK_MCP_TOKEN_FILE", ".token")
        token = Path(token_file).read_text(encoding="utf-8").strip()
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}
    ) as http_client:
        async with streamable_http_client(
            url, http_client=http_client
        ) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                yield client
