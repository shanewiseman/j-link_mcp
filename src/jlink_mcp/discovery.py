"""USB, serial, tool, and capability discovery."""

from __future__ import annotations

import grp
import os
import platform
import re
import shutil
from pathlib import Path

import pyudev

from .config import Settings
from .models import (
    BoardCapabilities,
    CapabilityAvailability,
    CapabilityManifest,
    CapabilityState,
    ProbeCapabilities,
    TargetCore,
    ToolAvailability,
    USBDevice,
)
from .profiles import GIGA_R1

SEGGER_TOOLS = (
    "DDConditionerExe",
    "DevProExe",
    "JFlashExe",
    "JFlashLiteExe",
    "JFlashSPICLExe",
    "JFlashSPIExe",
    "JLinkConfigExe",
    "JLinkConnServerExe",
    "JLinkExe",
    "JLinkGDBServerCLExe",
    "JLinkGDBServerExe",
    "JLinkGUIServerExe",
    "JLinkLicenseManagerExe",
    "JLinkRTTClientExe",
    "JLinkRTTLoggerExe",
    "JLinkRTTViewerExe",
    "JLinkRegistrationExe",
    "JLinkSWOViewerCLExe",
    "JLinkRemoteServerCLExe",
    "JLinkRemoteServerExe",
    "JLinkSWOViewerExe",
    "JLinkUSBWebServerExe",
    "JLinkXVCDServerExe",
    "JMemExe",
    "JRunExe",
    "JScopeExe",
    "JTAGLoadExe",
)


def _clean_hex(value: str | None) -> str:
    return (value or "").lower().removeprefix("0x").zfill(4)


def _device_nodes(device: pyudev.Device) -> list[str]:
    nodes: set[str] = set()
    if device.device_node:
        nodes.add(device.device_node)
    for child in device.children:
        if child.device_node:
            nodes.add(child.device_node)
    return sorted(nodes)


def _attribute(device: pyudev.Device, name: str) -> str | None:
    """Read a sysfs USB attribute when the container has no udev database."""
    try:
        value = device.attributes.get(name)
    except (KeyError, OSError):
        return None
    if isinstance(value, bytes):
        return value.decode(errors="replace").strip()
    return str(value).strip() if value is not None else None


def discover_usb_devices() -> list[USBDevice]:
    context = pyudev.Context()
    devices: list[USBDevice] = []
    for device in context.list_devices(subsystem="usb", DEVTYPE="usb_device"):
        vendor_id = _clean_hex(
            device.properties.get("ID_VENDOR_ID") or _attribute(device, "idVendor")
        )
        product_id = _clean_hex(
            device.properties.get("ID_MODEL_ID") or _attribute(device, "idProduct")
        )
        if vendor_id not in {"1366", "2341"}:
            continue
        kind = "jlink" if vendor_id == "1366" else "arduino"
        serial = (
            device.properties.get("ID_SERIAL_SHORT")
            or _attribute(device, "serial")
        )
        if isinstance(serial, bytes):
            serial = serial.decode(errors="replace")
        devices.append(
            USBDevice(
                kind=kind,
                vendor_id=vendor_id,
                product_id=product_id,
                manufacturer=device.properties.get("ID_VENDOR_FROM_DATABASE")
                or device.properties.get("ID_VENDOR")
                or _attribute(device, "manufacturer"),
                product=device.properties.get("ID_MODEL_FROM_DATABASE")
                or device.properties.get("ID_MODEL")
                or _attribute(device, "product"),
                serial=serial,
                bus=device.properties.get("BUSNUM") or _attribute(device, "busnum"),
                address=device.properties.get("DEVNUM") or _attribute(device, "devnum"),
                sys_path=device.sys_path,
                device_nodes=_device_nodes(device),
            )
        )
    return devices


def _serial_port(device: USBDevice, settings: Settings) -> str | None:
    for node in device.device_nodes:
        if re.fullmatch(r"/dev/tty(?:ACM|USB)\d+", node):
            host_node = settings.host_dev_root / Path(node).relative_to("/dev")
            return str(host_node) if host_node.exists() else node
    if device.serial:
        by_id = Path("/dev/serial/by-id")
        if by_id.exists():
            for candidate in by_id.iterdir():
                if device.serial in candidate.name:
                    return str(candidate)
    return None


def discover_tools(settings: Settings) -> list[ToolAvailability]:
    tools: list[ToolAvailability] = []
    for name in SEGGER_TOOLS:
        candidate = settings.segger_root / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            tools.append(
                ToolAvailability(
                    name=name,
                    state=CapabilityState.AVAILABLE,
                    path=str(candidate.resolve()),
                    version=_segger_version(settings.segger_root),
                )
            )
        else:
            tools.append(
                ToolAvailability(
                    name=name,
                    state=CapabilityState.UNAVAILABLE,
                    reason=f"not found below {settings.segger_root}",
                )
            )
    for name, configured in (
        ("arduino-cli", settings.arduino_cli),
        ("arm-none-eabi-gdb", settings.arm_gdb),
        (
            "arm-none-eabi-objcopy",
            str(Path(settings.arm_gdb).parent / "arm-none-eabi-objcopy"),
        ),
        (
            "arm-none-eabi-objdump",
            str(Path(settings.arm_gdb).parent / "arm-none-eabi-objdump"),
        ),
        (
            "arm-none-eabi-nm",
            str(Path(settings.arm_gdb).parent / "arm-none-eabi-nm"),
        ),
        (
            "openocd",
            str(
                settings.arduino_data_root
                / "packages/arduino/tools/openocd/0.11.0-arduino2/bin/openocd"
            ),
        ),
        (
            "dfu-util",
            str(
                settings.arduino_data_root
                / "packages/arduino/tools/dfu-util/0.10.0-arduino1/dfu-util"
            ),
        ),
        (
            "imgtool",
            str(
                settings.arduino_data_root
                / "packages/arduino/tools/imgtool/1.8.0-arduino.2/imgtool"
            ),
        ),
        ("Xvfb", shutil.which("Xvfb")),
        ("xdotool", shutil.which("xdotool")),
        ("tesseract", shutil.which("tesseract")),
        ("scrot", shutil.which("scrot") or shutil.which("import")),
        (
            "Ozone",
            str(settings.ozone_root / "Ozone") if settings.ozone_root else None,
        ),
        (
            "SystemView",
            str(settings.systemview_root / "SystemView")
            if settings.systemview_root
            else None,
        ),
    ):
        path = configured if configured and Path(configured).is_file() else shutil.which(name)
        tools.append(
            ToolAvailability(
                name=name,
                state=(
                    CapabilityState.AVAILABLE
                    if path
                    else CapabilityState.UNAVAILABLE
                ),
                path=str(path) if path else None,
                reason=None if path else "not installed or not in PATH",
            )
        )
    return tools


def _segger_version(root: Path) -> str | None:
    match = re.search(r"(?:V|JLink[_-]?V?)(\d)(\d{2})", root.name, re.IGNORECASE)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    release_notes = root / "Doc" / "ReleaseNotes" / "ReleaseNotes.html"
    if release_notes.exists():
        try:
            head = release_notes.read_text(encoding="utf-8", errors="ignore")[:65536]
            if match := re.search(r"Version\s+V(\d+\.\d+[a-z]?)", head):
                return match.group(1)
        except OSError:
            pass
    return None


def capability_manifest(settings: Settings) -> CapabilityManifest:
    usb_devices = discover_usb_devices()
    probes: list[ProbeCapabilities] = []
    boards: list[BoardCapabilities] = []
    for usb in usb_devices:
        if usb.kind == "jlink" and usb.serial:
            model = "SEGGER J-Link"
            if usb.product_id == "1020":
                model = "SEGGER J-Link (EDU Mini V2 compatible USB identity)"
            probes.append(
                ProbeCapabilities(
                    serial=usb.serial,
                    model=model,
                    usb=usb,
                    interfaces=["SWD"],
                    max_swd_speed_khz=4000 if usb.product_id == "1020" else None,
                    max_swo_speed_khz=4000 if usb.product_id == "1020" else None,
                    target_power=False if usb.product_id == "1020" else None,
                    trace=["SWO", "RTT"],
                )
            )
        elif (
            usb.kind == "arduino"
            and usb.vendor_id == GIGA_R1.usb_vid
            and usb.product_id in GIGA_R1.usb_pids
        ):
            boards.append(
                BoardCapabilities(
                    serial=usb.serial,
                    model=GIGA_R1.display_name,
                    fqbn=GIGA_R1.fqbn,
                    mcu=GIGA_R1.mcu,
                    cores=[TargetCore.M7, TargetCore.M4],
                    usb=usb,
                    serial_port=_serial_port(usb, settings),
                )
            )

    unique_pair = len(probes) == 1 and len(boards) == 1
    limitations = [
        "Probe capabilities are dynamically constrained by model, firmware, and licenses.",
        "Board identity inferred over USB must be confirmed by target MCU identity before writes.",
        "Direct J-Link SDK calls are unavailable until a licensed SDK is mounted.",
    ]
    if probes and probes[0].usb.product_id == "1020":
        limitations.append(
            "EDU Mini use is restricted to qualifying non-profit educational work."
        )
    tools = discover_tools(settings)
    available = {tool.name for tool in tools if tool.state == CapabilityState.AVAILABLE}
    workflows = {
        "preflight": CapabilityState.AVAILABLE,
        "build_firmware": (
            CapabilityState.AVAILABLE
            if "arduino-cli" in available
            else CapabilityState.UNAVAILABLE
        ),
        "flash_verify": (
            CapabilityState.AVAILABLE
            if "JLinkExe" in available
            else CapabilityState.UNAVAILABLE
        ),
        "debug": (
            CapabilityState.AVAILABLE
            if {"JLinkGDBServerCLExe", "arm-none-eabi-gdb"} <= available
            else CapabilityState.UNAVAILABLE
        ),
        "gui": (
            CapabilityState.AVAILABLE
            if settings.enable_gui and {"Xvfb", "xdotool"} <= available
            else CapabilityState.UNAVAILABLE
        ),
        "sdk": CapabilityState.UNAVAILABLE,
        "serial": (
            CapabilityState.AVAILABLE
            if any(board.serial_port for board in boards)
            else CapabilityState.UNAVAILABLE
        ),
        "rtt": (
            CapabilityState.AVAILABLE
            if "JLinkRTTLoggerExe" in available
            else CapabilityState.UNAVAILABLE
        ),
        "swo_itm": (
            CapabilityState.AVAILABLE
            if probes and probes[0].max_swo_speed_khz
            else CapabilityState.UNAVAILABLE
        ),
        "dual_core_deploy": (
            CapabilityState.AVAILABLE
            if "JLinkExe" in available and "arduino-cli" in available
            else CapabilityState.UNAVAILABLE
        ),
        "backup_restore": (
            CapabilityState.AVAILABLE
            if "JLinkExe" in available
            else CapabilityState.UNAVAILABLE
        ),
    }
    workflows.update(
        {
            "hardware_preflight": workflows["flash_verify"],
            "dual_core_debug_prepare": workflows["flash_verify"],
            "boot_and_observe": (
                CapabilityState.AVAILABLE
                if workflows["flash_verify"] == CapabilityState.AVAILABLE
                and workflows["serial"] == CapabilityState.AVAILABLE
                else CapabilityState.UNAVAILABLE
            ),
            "debug_fixture": (
                CapabilityState.AVAILABLE
                if workflows["debug"] == CapabilityState.AVAILABLE
                and workflows["serial"] == CapabilityState.AVAILABLE
                else CapabilityState.UNAVAILABLE
            ),
            "crash_capture": (
                CapabilityState.AVAILABLE
                if workflows["debug"] == CapabilityState.AVAILABLE
                and workflows["serial"] == CapabilityState.AVAILABLE
                else CapabilityState.UNAVAILABLE
            ),
            "firmware_comparison": workflows["flash_verify"],
            "rtt_capture": workflows["rtt"],
            "regression_execution": (
                CapabilityState.AVAILABLE
                if workflows["dual_core_deploy"] == CapabilityState.AVAILABLE
                and workflows["debug"] == CapabilityState.AVAILABLE
                and workflows["serial"] == CapabilityState.AVAILABLE
                else CapabilityState.UNAVAILABLE
            ),
            "validation_report": CapabilityState.AVAILABLE,
        }
    )

    def detail(
        name: str, dependencies: list[str], unavailable_reason: str
    ) -> CapabilityAvailability:
        state = workflows[name]
        return CapabilityAvailability(
            state=state,
            dependencies=dependencies,
            reason=None if state == CapabilityState.AVAILABLE else unavailable_reason,
        )

    return CapabilityManifest(
        host_os=platform.system(),
        host_arch=platform.machine(),
        probes=probes,
        boards=boards,
        tools=tools,
        workflows=workflows,
        workflow_details={
            "preflight": detail("preflight", [], "internal workflow unavailable"),
            "build_firmware": detail(
                "build_firmware", ["arduino-cli"], "Arduino CLI is not installed"
            ),
            "flash_verify": detail(
                "flash_verify", ["JLinkExe"], "J-Link Commander is not installed"
            ),
            "debug": detail(
                "debug",
                ["JLinkGDBServerCLExe", "arm-none-eabi-gdb"],
                "J-Link GDB Server and Arm GDB are both required",
            ),
            "gui": detail(
                "gui", ["Xvfb", "xdotool"],
                "GUI is disabled or Xvfb/xdotool is missing",
            ),
            "sdk": CapabilityAvailability(
                state=CapabilityState.UNAVAILABLE,
                dependencies=["licensed J-Link SDK mount"],
                reason="No separately licensed J-Link SDK package is mounted",
            ),
            "serial": detail(
                "serial", ["GIGA USB CDC"], "No accessible GIGA serial node was discovered"
            ),
            "rtt": detail(
                "rtt", ["JLinkRTTLoggerExe"], "J-Link RTT Logger is not installed"
            ),
            "swo_itm": detail(
                "swo_itm", ["probe SWO support", "SWO wire"],
                "The selected probe does not report SWO support",
            ),
            "dual_core_deploy": detail(
                "dual_core_deploy", ["JLinkExe", "arduino-cli"],
                "Commander and Arduino CLI are both required",
            ),
            "backup_restore": detail(
                "backup_restore", ["JLinkExe"], "J-Link Commander is not installed"
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
                ["JLinkGDBServerCLExe", "arm-none-eabi-gdb", "GIGA USB CDC"],
                "Managed GDB and GIGA serial are both required",
            ),
            "crash_capture": detail(
                "crash_capture",
                ["JLinkGDBServerCLExe", "arm-none-eabi-gdb", "GIGA USB CDC"],
                "Managed GDB and the fixture fault trigger are required",
            ),
            "firmware_comparison": detail(
                "firmware_comparison",
                ["JLinkExe"],
                "J-Link Commander is not installed",
            ),
            "rtt_capture": detail(
                "rtt_capture",
                ["JLinkRTTLoggerExe", "ELF _SEGGER_RTT symbol"],
                "RTT Logger or a concrete control-block symbol is unavailable",
            ),
            "regression_execution": detail(
                "regression_execution",
                ["Arduino CLI", "Commander", "managed GDB", "GIGA USB CDC"],
                "The complete build, flash, debug, and observation stack is required",
            ),
            "validation_report": detail(
                "validation_report", ["persistent state"], "Persistent state is unavailable"
            ),
        },
        features={
            "target_power": CapabilityAvailability(
                state=CapabilityState.UNAVAILABLE,
                reason="J-Link EDU Mini V2 cannot supply target power",
            ),
            "etm_trace": CapabilityAvailability(
                state=CapabilityState.UNAVAILABLE,
                reason="J-Link EDU Mini V2 has no high-speed trace capture interface",
            ),
            "swo_wire": CapabilityAvailability(
                state=CapabilityState.UNKNOWN,
                dependencies=["physical SWO connection"],
                reason="SWO wiring cannot be inferred from USB discovery; validate by capture",
            ),
            "ozone": CapabilityAvailability(
                state=(CapabilityState.AVAILABLE if "Ozone" in available else CapabilityState.UNAVAILABLE),
                reason=None if "Ozone" in available else "Optional Ozone package is not mounted",
            ),
            "systemview": CapabilityAvailability(
                state=(CapabilityState.AVAILABLE if "SystemView" in available else CapabilityState.UNAVAILABLE),
                reason=None if "SystemView" in available else "Optional SystemView package is not mounted",
            ),
            "semihosting": CapabilityAvailability(
                state=(
                    CapabilityState.AVAILABLE
                    if workflows["debug"] == CapabilityState.AVAILABLE
                    else CapabilityState.UNAVAILABLE
                ),
                dependencies=["J-Link GDB Server telnet channel"],
                reason=(
                    None
                    if workflows["debug"] == CapabilityState.AVAILABLE
                    else "Managed J-Link GDB Server is unavailable"
                ),
            ),
            "itm": CapabilityAvailability(
                state=workflows["swo_itm"],
                dependencies=["probe SWO support", "physical SWO wire"],
                reason=(
                    None
                    if workflows["swo_itm"] == CapabilityState.AVAILABLE
                    else "Probe SWO support is unavailable"
                ),
            ),
        },
        raw_surfaces=[
            "J-Link Commander command files",
            "J-Link command strings",
            "GDB/MI and validated monitor commands",
            "allowlisted SEGGER application arguments",
        ],
        atomic_tools=[
            "list_jlink_probes", "connect_target", "disconnect_target",
            "get_probe_information", "reset_target", "halt_target", "run_target",
            "step_target", "read_memory", "write_memory", "read_register",
            "write_register", "set_breakpoint", "clear_breakpoint",
            "set_watchpoint", "clear_watchpoint", "erase_flash", "verify_binary",
            "serial_exchange", "capture_serial", "swo_control", "start_gdb_session",
            "gdb_command", "capture_gdb_channel", "exchange_gdb_channel",
            "stop_gdb_session", "inspect_elf", "run_segger_application",
            "launch_segger_gui", "gui_session_info", "gui_keys", "gui_click",
            "gui_screenshot", "gui_ocr", "gui_accessibility_tree",
            "gui_image_match", "stop_segger_gui",
        ],
        limitations=limitations,
        unique_pair=unique_pair,
        selected_probe_serial=probes[0].serial if unique_pair else None,
        selected_board_serial=boards[0].serial if unique_pair else None,
    )


def current_groups() -> set[str]:
    group_ids = os.getgroups()
    return {grp.getgrgid(group_id).gr_name for group_id in group_ids}
