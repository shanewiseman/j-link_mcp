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
    USBDevice,
)
from jlink_mcp.profiles import CoreProfile, TargetProfile, TargetRegistry


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
        gdb_client="gdb-client",
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
        kind="usb",
        vendor_id="1234",
        product_id="5678",
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
                model="Sample target",
                target_profile="sample_target",
                mcu="SAMPLE",
                cores=["primary", "secondary"],
                usb=board_usb,
                serial_port="/dev/ttyACM0",
            )
        ],
        unique_pair=True,
        selected_probe_serial="000802008248",
        selected_board_serial="0045002B3333511632363530",
    )


@pytest.fixture
def target_registry() -> TargetRegistry:
    registry = TargetRegistry()
    registry.register_profile(
        TargetProfile(
            id="sample_target",
            display_name="Sample target",
            cores={
                "primary": CoreProfile(
                    id="primary",
                    jlink_device="SAMPLE_PRIMARY",
                    expected_core="Cortex-M7",
                    expected_cpuid=0x411FC271,
                ),
                "secondary": CoreProfile(
                    id="secondary",
                    jlink_device="SAMPLE_SECONDARY",
                    expected_core="Cortex-M4",
                    expected_cpuid=0x410FC241,
                ),
            },
            default_core="primary",
            expected_dpidr=0x6BA02477,
        )
    )
    return registry


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
