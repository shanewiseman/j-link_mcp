from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from jlink_mcp_arduino_giga.config import ArduinoGigaConfig
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from jlink_mcp.config import Settings
from jlink_mcp.models import CommandResult

HIL_ENABLED = os.environ.get("JLINK_MCP_HIL") == "1"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    segger = tmp_path / "segger"
    for directory in (workspace, state, segger):
        directory.mkdir(parents=True)
    token_file = tmp_path / "token"
    token_file.write_text("test-token\n", encoding="utf-8")
    result = Settings(
        repository_root=workspace,
        workspace_root=workspace,
        state_root=state,
        segger_root=segger,
        token_file=token_file,
        gdb_client="arm-none-eabi-gdb",
        default_timeout_seconds=0.2,
    )
    result.ensure_directories()
    return result


@pytest.fixture
def giga_config(tmp_path: Path) -> ArduinoGigaConfig:
    data_root = tmp_path / "arduino-data"
    user_root = tmp_path / "arduino-user"
    data_root.mkdir()
    user_root.mkdir()
    return ArduinoGigaConfig(
        arduino_cli="arduino-cli",
        data_root=data_root,
        user_root=user_root,
    )


def make_result(
    *,
    parsed: dict | None = None,
    stdout: str = "",
    stderr: str = "",
    return_code: int | None = 0,
    timed_out: bool = False,
    backend: str = "fake",
) -> CommandResult:
    now = datetime.now(UTC)
    return CommandResult(
        operation_id=os.urandom(16).hex(),
        backend=backend,
        command=["fake"],
        started_at=now,
        finished_at=now,
        duration_ms=0,
        return_code=return_code,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        parsed=parsed or {},
    )


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
    async with (
        httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write, _),
        ClientSession(read, write) as client,
    ):
        await client.initialize()
        yield client
