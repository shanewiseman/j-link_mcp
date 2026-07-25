from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


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
        token = open(token_file, encoding="utf-8").read().strip()
    async with streamablehttp_client(
        url, headers={"Authorization": f"Bearer {token}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield client


@pytest.fixture
def selector():
    return {
        "probe_serial": os.environ.get("JLINK_MCP_PROBE_SERIAL", "000802008248"),
        "board_serial": os.environ.get(
            "JLINK_MCP_BOARD_SERIAL", "0045002B3333511632363530"
        ),
        "target_profile": "arduino_giga_r1",
        "core": "m7",
        "interface": "SWD",
        "speed_khz": 4000,
    }
