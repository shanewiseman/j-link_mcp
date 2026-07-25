from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from jlink_mcp.config import Settings
from jlink_mcp.models import (
    BoardCapabilities,
    CapabilityManifest,
    CommandResult,
    DeviceSelector,
    ProbeCapabilities,
    USBDevice,
)
from jlink_mcp_arduino_giga.config import ArduinoGigaConfig

PROBE = "000802008248"
BOARD = "0045002B3333511632363530"
HIL_ENABLED = os.environ.get("JLINK_MCP_HIL") == "1"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    segger = tmp_path / "segger"
    host_dev = tmp_path / "dev"
    sys_usb = tmp_path / "sys-usb"
    for directory in (workspace, state, segger, host_dev, sys_usb):
        directory.mkdir(parents=True)
    token_file = tmp_path / "token"
    token_file.write_text("test-token\n", encoding="utf-8")
    result = Settings(
        repository_root=workspace,
        workspace_root=workspace,
        state_root=state,
        segger_root=segger,
        host_dev_root=host_dev,
        sys_usb_root=sys_usb,
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


@pytest.fixture
def manifest() -> CapabilityManifest:
    probe_usb = USBDevice(
        kind="jlink",
        vendor_id="1366",
        product_id="1020",
        serial=PROBE,
        device_nodes=["/dev/bus/usb/001/002"],
    )
    board_usb = USBDevice(
        kind="usb",
        vendor_id="2341",
        product_id="0266",
        serial=BOARD,
        device_nodes=["/dev/ttyACM0"],
    )
    return CapabilityManifest(
        host_os="linux",
        host_arch="x86_64",
        probes=[
            ProbeCapabilities(
                serial=PROBE,
                model="J-Link EDU Mini V2",
                usb=probe_usb,
                max_swd_speed_khz=4000,
                max_swo_speed_khz=4000,
                target_power=False,
            )
        ],
        boards=[
            BoardCapabilities(
                serial=BOARD,
                model="Arduino GIGA R1 WiFi",
                target_profile="arduino_giga_r1",
                mcu="STM32H747XI",
                cores=["m7", "m4"],
                usb=board_usb,
                serial_port="/dev/ttyACM0",
                metadata={"fqbn": "arduino:mbed_giga:giga"},
            )
        ],
        unique_pair=True,
        selected_probe_serial=PROBE,
        selected_board_serial=BOARD,
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


def selector(core: str = "m7") -> DeviceSelector:
    return DeviceSelector(
        probe_serial=PROBE,
        board_serial=BOARD,
        target_profile="arduino_giga_r1",
        core=core,
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
    async with streamablehttp_client(
        url, headers={"Authorization": f"Bearer {token}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield client
