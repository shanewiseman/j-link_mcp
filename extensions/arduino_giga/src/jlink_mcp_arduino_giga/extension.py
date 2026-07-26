"""Registration lifecycle for the first-party Arduino GIGA extension."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from mcp.types import ToolAnnotations

from jlink_mcp.discovery import serial_port_for_usb
from jlink_mcp.doctor import current_groups, tool_ok, tool_path
from jlink_mcp.extensions import (
    EXTENSION_API_VERSION,
    CapabilityContribution,
    ExtensionContext,
)
from jlink_mcp.models import (
    BoardCapabilities,
    CapabilityAvailability,
    CapabilityManifest,
    CapabilityState,
    DependencyCheck,
    ToolAvailability,
)

from .config import ArduinoGigaConfig
from .models import BuildResult, DeviceSelector, ValidationReport
from .profiles import GIGA_R1, TargetCore
from .workflows import ArduinoGigaWorkflows

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
MUTATING = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)


class ArduinoGigaExtension:
    id = "arduino_giga"
    version = "0.1.0"
    api_version = EXTENSION_API_VERSION
    dependencies: tuple[str, ...] = ()
    config_model = ArduinoGigaConfig

    def register(self, context: ExtensionContext) -> None:
        config = ArduinoGigaConfig.model_validate(context.config)
        service = context.services.jlink
        workflows = ArduinoGigaWorkflows(service, config)
        context.register_target_profile(GIGA_R1)

        def detect_board(usb):
            metadata = GIGA_R1.metadata
            if (
                usb.vendor_id != metadata["usb_vid"]
                or usb.product_id not in metadata["usb_pids"]
            ):
                return None
            return BoardCapabilities(
                serial=usb.serial,
                model=GIGA_R1.display_name,
                target_profile=GIGA_R1.id,
                mcu=str(metadata["mcu"]),
                cores=[str(item) for item in GIGA_R1.cores],
                usb=usb,
                serial_port=serial_port_for_usb(usb, context.services.paths),
                metadata={"fqbn": str(metadata["fqbn"])},
            )

        context.register_board_detector("usb", detect_board)
        context.register_capability_provider(
            lambda manifest: _capabilities(config, manifest)
        )
        context.register_dependency_provider(
            lambda manifest: _dependencies(config, manifest, service)
        )
        context.publish_service("workflows", workflows)
        context.publish_service("config", config)
        context.publish_service("profile", GIGA_R1)
        self._register_tools(context, workflows)

    def shutdown(self) -> None:
        return None

    @staticmethod
    def _register_tools(
        context: ExtensionContext, workflows: ArduinoGigaWorkflows
    ) -> None:
        @context.register_tool(annotations=READ_ONLY)
        async def build_giga_firmware(
            sketch_path: str,
            core: TargetCore,
            flash_split: str = "75_25",
            clean: bool = True,
        ) -> BuildResult:
            """Compile a GIGA M7 or M4 sketch and register all artifacts."""
            return await workflows.build_firmware(
                sketch_path, core=core, flash_split=flash_split, clean=clean
            )

        @context.register_tool(annotations=MUTATING)
        async def validate_giga_fixture(
            selector: DeviceSelector | None = None,
            m7_sketch: str = "firmware/giga_hil/m7",
            m4_sketch: str = "firmware/giga_hil/m4",
        ) -> ValidationReport:
            """Run the MCP-owned GIGA fixture build/flash validation workflow."""
            return await workflows.validate_fixture(
                selector=selector, m7_sketch=m7_sketch, m4_sketch=m4_sketch
            )

        @context.register_tool(annotations=MUTATING)
        async def hardware_preflight(
            selector: DeviceSelector | None = None,
            prepare_dual_core: bool = False,
        ) -> dict[str, Any]:
            """Identify both cores and snapshot state, optionally releasing held M4."""
            return await workflows.hardware_preflight(
                selector=selector, prepare_dual_core=prepare_dual_core
            )

        @context.register_tool(annotations=MUTATING)
        async def prepare_giga_dual_core_debug(
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Transiently release an option-held GIGA M4 and verify both cores."""
            return await workflows.prepare_giga_dual_core_debug(selector=selector)

        @context.register_tool(annotations=MUTATING)
        async def deploy_dual_core_firmware(
            selector: DeviceSelector | None = None,
            m7_sketch: str = "firmware/giga_hil/m7",
            m4_sketch: str = "firmware/giga_hil/m4",
            flash_split: str = "75_25",
        ) -> dict[str, Any]:
            """Build, flash, verify, reset, and run both GIGA cores."""
            return await workflows.dual_core_deploy(
                selector=selector,
                m7_sketch=m7_sketch,
                m4_sketch=m4_sketch,
                flash_split=flash_split,
            )

        @context.register_tool(annotations=MUTATING)
        async def boot_and_observe(
            selector: DeviceSelector | None = None,
            m7_elf_path: str | None = None,
            m4_elf_path: str | None = None,
        ) -> dict[str, Any]:
            """Reset/run both cores and validate manifests, heartbeat, self-test, and RPC."""
            return await workflows.boot_and_observe(
                selector=selector,
                m7_elf_path=m7_elf_path,
                m4_elf_path=m4_elf_path,
            )

        @context.register_tool(annotations=MUTATING)
        async def assert_debug_fixture(
            elf_path: str,
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Assert symbolic breakpoint/watchpoint, registers, stack, memory, and step."""
            return await workflows.debug_fixture(elf_path, selector=selector)

        @context.register_tool(annotations=MUTATING)
        async def capture_controlled_crash(
            elf_path: str,
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Trigger, capture, analyze, and recover a fixture HardFault."""
            return await workflows.crash_capture(elf_path, selector=selector)


def _tool_availability(name: str, configured: str | Path) -> ToolAvailability:
    raw = str(configured)
    path = raw if Path(raw).is_file() else shutil.which(raw)
    return ToolAvailability(
        name=name,
        state=CapabilityState.AVAILABLE if path else CapabilityState.UNAVAILABLE,
        path=str(path) if path else None,
        reason=None if path else "not installed or not in PATH",
    )


def _extension_tools(config: ArduinoGigaConfig) -> list[ToolAvailability]:
    compiler = (
        config.data_root / "packages/arduino/tools/arm-none-eabi-gcc/7-2017q4/bin"
    )
    return [
        _tool_availability("arduino-cli", config.arduino_cli),
        *[
            _tool_availability(name, compiler / name)
            for name in (
                "arm-none-eabi-gdb",
                "arm-none-eabi-objcopy",
                "arm-none-eabi-objdump",
                "arm-none-eabi-nm",
            )
        ],
        _tool_availability(
            "openocd",
            config.data_root
            / "packages/arduino/tools/openocd/0.11.0-arduino2/bin/openocd",
        ),
        _tool_availability(
            "dfu-util",
            config.data_root
            / "packages/arduino/tools/dfu-util/0.10.0-arduino1/dfu-util",
        ),
        _tool_availability(
            "imgtool",
            config.data_root / "packages/arduino/tools/imgtool/1.8.0-arduino.2/imgtool",
        ),
    ]


def _capabilities(
    config: ArduinoGigaConfig, manifest: CapabilityManifest
) -> CapabilityContribution:
    tools = _extension_tools(config)
    available = {
        tool.name
        for tool in [*manifest.tools, *tools]
        if tool.state == CapabilityState.AVAILABLE
    }
    serial_available = any(
        board.target_profile == GIGA_R1.id and board.serial_port
        for board in manifest.boards
    )
    flash_available = "JLinkExe" in available
    build_available = "arduino-cli" in available
    debug_available = {
        "JLinkGDBServerCLExe",
        "gdb-client",
    } <= available

    def state(value: bool) -> CapabilityState:
        return CapabilityState.AVAILABLE if value else CapabilityState.UNAVAILABLE

    workflows = {
        "build_firmware": state(build_available),
        "dual_core_deploy": state(build_available and flash_available),
        "hardware_preflight": state(flash_available),
        "dual_core_debug_prepare": state(flash_available),
        "boot_and_observe": state(flash_available and serial_available),
        "debug_fixture": state(debug_available and serial_available),
        "crash_capture": state(debug_available and serial_available),
        "regression_execution": state(
            build_available and flash_available and debug_available and serial_available
        ),
    }

    def detail(name: str, dependencies: list[str], reason: str):
        return CapabilityAvailability(
            state=workflows[name],
            dependencies=dependencies,
            reason=None if workflows[name] == CapabilityState.AVAILABLE else reason,
        )

    return CapabilityContribution(
        tools=tools,
        workflows=workflows,
        workflow_details={
            "build_firmware": detail(
                "build_firmware", ["arduino-cli"], "Arduino CLI is unavailable"
            ),
            "dual_core_deploy": detail(
                "dual_core_deploy",
                ["JLinkExe", "arduino-cli"],
                "Commander and Arduino CLI are both required",
            ),
            "hardware_preflight": detail(
                "hardware_preflight",
                ["JLinkExe", "live M7/M4 access"],
                "J-Link Commander or live target access is unavailable",
            ),
            "dual_core_debug_prepare": detail(
                "dual_core_debug_prepare",
                ["JLinkExe", "live M7 access"],
                "J-Link Commander or live M7 access is unavailable",
            ),
            "boot_and_observe": detail(
                "boot_and_observe",
                ["JLinkExe", "GIGA USB CDC"],
                "Commander and an accessible GIGA serial channel are required",
            ),
            "debug_fixture": detail(
                "debug_fixture",
                ["JLinkGDBServerCLExe", "gdb-client", "GIGA USB CDC"],
                "Managed GDB and GIGA serial are both required",
            ),
            "crash_capture": detail(
                "crash_capture",
                ["JLinkGDBServerCLExe", "gdb-client", "GIGA USB CDC"],
                "Managed GDB and the fixture fault trigger are required",
            ),
            "regression_execution": detail(
                "regression_execution",
                ["Arduino CLI", "Commander", "managed GDB", "GIGA USB CDC"],
                "The complete GIGA build, flash, debug, and observation stack is required",
            ),
        },
        limitations=[
            "The GIGA secondary core may require transient runtime release before debug."
        ],
    )


def _dependencies(
    config: ArduinoGigaConfig,
    manifest: CapabilityManifest,
    service: Any,
) -> list[DependencyCheck]:
    tools = {tool.name: tool for tool in _extension_tools(config)}
    platform_root = config.data_root / "packages/arduino/hardware/mbed_giga/4.6.0"
    boards = [board for board in manifest.boards if board.target_profile == GIGA_R1.id]
    identities: dict[str, dict[str, Any]] = {}
    for entry in service.store.list_operations(limit=1000):
        target = entry["payload"].get("result", {}).get("target_identity", {})
        core = target.get("core")
        if (
            target.get("target_profile") == GIGA_R1.id
            and core
            and core not in identities
            and target.get("cpuid")
        ):
            identities[str(core)] = target
    voltages = [
        value.get("target_voltage")
        for value in identities.values()
        if isinstance(value.get("target_voltage"), (int, float))
    ]
    groups = current_groups()
    checks = [
        DependencyCheck(
            name="arduino-cli",
            ok=tool_ok(tools, "arduino-cli"),
            observed=tool_path(tools, "arduino-cli"),
            expected="arduino-cli 1.5.1",
        ),
        *[
            DependencyCheck(
                name=name,
                ok=tool_ok(tools, name),
                observed=tool_path(tools, name),
                expected=f"pinned Arduino GIGA {name}",
            )
            for name in (
                "arm-none-eabi-gdb",
                "arm-none-eabi-objcopy",
                "arm-none-eabi-objdump",
                "arm-none-eabi-nm",
                "openocd",
                "dfu-util",
                "imgtool",
            )
        ],
        DependencyCheck(
            name="arduino-core-4.6.0",
            ok=(platform_root / "platform.txt").is_file(),
            observed=str(platform_root),
            expected="arduino:mbed_giga@4.6.0",
        ),
        DependencyCheck(
            name="giga-svd-m7",
            ok=(platform_root / "svd/STM32H747_CM7.svd").is_file(),
            observed=str(platform_root / "svd/STM32H747_CM7.svd"),
            expected="STM32H747_CM7.svd",
        ),
        DependencyCheck(
            name="giga-svd-m4",
            ok=(platform_root / "svd/STM32H747_CM4.svd").is_file(),
            observed=str(platform_root / "svd/STM32H747_CM4.svd"),
            expected="STM32H747_CM4.svd",
        ),
        DependencyCheck(
            name="giga-bootloader",
            ok=(platform_root / "bootloaders/GIGA/bootloader.hex").is_file(),
            observed=str(platform_root / "bootloaders/GIGA/bootloader.hex"),
            expected="Arduino GIGA bootloader asset (read-only validation input)",
        ),
        DependencyCheck(
            name="giga-attached",
            ok=bool(boards),
            observed=str([board.serial for board in boards]),
            expected="Arduino GIGA R1 USB identity",
        ),
        DependencyCheck(
            name="unique-pair",
            ok=len(manifest.probes) == 1 and len(boards) == 1,
            observed=f"{len(manifest.probes)} probe(s), {len(boards)} GIGA board(s)",
            expected="one J-Link and one GIGA",
            remediation="Specify stable serial selectors when more than one device exists.",
        ),
        DependencyCheck(
            name="dialout-group",
            ok="dialout" in groups or os.geteuid() == 0,
            observed=",".join(sorted(groups)),
            expected="dialout membership or accessible board serial device",
        ),
    ]
    for core_id, core_profile in GIGA_R1.cores.items():
        identity = identities.get(str(core_id), {})
        checks.append(
            DependencyCheck(
                name=f"target-{core_id}-identity",
                ok=_integer(identity.get("cpuid")) == core_profile.expected_cpuid,
                observed=str(identity or "no positive identity observation"),
                expected=(
                    f"{GIGA_R1.display_name} {str(core_id).upper()} CPUID "
                    f"0x{core_profile.expected_cpuid:08X}"
                ),
                remediation="Run hardware_preflight with the stable probe/board selector.",
            )
        )
    checks.append(
        DependencyCheck(
            name="target-voltage",
            ok=bool(voltages) and min(voltages) >= GIGA_R1.minimum_target_voltage,
            observed=str(voltages or "not observed"),
            expected=f"VTref >= {GIGA_R1.minimum_target_voltage:.1f} V",
            remediation="Power the target separately and connect VTref/GND.",
        )
    )
    return checks


def _integer(value: Any) -> int:
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return -1
