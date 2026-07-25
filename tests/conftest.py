from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jlink_mcp.config import Settings
from jlink_mcp.models import (
    BoardCapabilities,
    CapabilityManifest,
    CommandResult,
    ProbeCapabilities,
    TargetCore,
    USBDevice,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    segger = tmp_path / "segger"
    host_dev = tmp_path / "dev"
    sys_usb = tmp_path / "sys-usb"
    arduino = tmp_path / "arduino"
    for directory in (workspace, state, segger, host_dev, sys_usb, arduino):
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
        arduino_data_root=arduino,
        token_file=token_file,
        arduino_cli="arduino-cli",
        arm_gdb="arm-none-eabi-gdb",
        default_timeout_seconds=0.2,
    )
    result.ensure_directories()
    return result


@pytest.fixture
def manifest() -> CapabilityManifest:
    probe_usb = USBDevice(
        kind="jlink",
        vendor_id="1366",
        product_id="1020",
        serial="000802008248",
        device_nodes=["/dev/bus/usb/001/002"],
    )
    board_usb = USBDevice(
        kind="arduino",
        vendor_id="2341",
        product_id="0266",
        serial="0045002B3333511632363530",
        device_nodes=["/dev/ttyACM0"],
    )
    return CapabilityManifest(
        host_os="linux",
        host_arch="x86_64",
        probes=[
            ProbeCapabilities(
                serial="000802008248",
                model="J-Link EDU Mini V2",
                usb=probe_usb,
                max_swd_speed_khz=15000,
                max_swo_speed_khz=4000,
                target_power=False,
            )
        ],
        boards=[
            BoardCapabilities(
                serial="0045002B3333511632363530",
                model="Arduino GIGA R1 WiFi",
                fqbn="arduino:mbed_giga:giga",
                mcu="STM32H747XI",
                cores=[TargetCore.M7, TargetCore.M4],
                usb=board_usb,
                serial_port="/dev/ttyACM0",
            )
        ],
        unique_pair=True,
        selected_probe_serial="000802008248",
        selected_board_serial="0045002B3333511632363530",
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
