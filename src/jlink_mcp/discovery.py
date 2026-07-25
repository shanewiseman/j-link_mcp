"""Target-neutral USB, tool, probe, board, and capability discovery."""

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
    CapabilityAvailability,
    CapabilityManifest,
    CapabilityState,
    ProbeCapabilities,
    ToolAvailability,
    USBDevice,
)
from .profiles import TargetRegistry

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
    try:
        value = device.attributes.get(name)
    except (KeyError, OSError):
        return None
    if isinstance(value, bytes):
        return value.decode(errors="replace").strip()
    return str(value).strip() if value is not None else None


def discover_usb_devices() -> list[USBDevice]:
    """Return USB identities for probe discovery and registered board detectors."""

    context = pyudev.Context()
    devices: list[USBDevice] = []
    for device in context.list_devices(subsystem="usb", DEVTYPE="usb_device"):
        vendor_id = _clean_hex(
            device.properties.get("ID_VENDOR_ID") or _attribute(device, "idVendor")
        )
        product_id = _clean_hex(
            device.properties.get("ID_MODEL_ID") or _attribute(device, "idProduct")
        )
        serial = device.properties.get("ID_SERIAL_SHORT") or _attribute(
            device, "serial"
        )
        if isinstance(serial, bytes):
            serial = serial.decode(errors="replace")
        devices.append(
            USBDevice(
                kind="jlink" if vendor_id == "1366" else "usb",
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


def serial_port_for_usb(device: USBDevice, settings: Settings) -> str | None:
    """Resolve a discovered USB serial node through the configured host view."""

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
    segger_version = _segger_version(settings.segger_root)
    for name in SEGGER_TOOLS:
        candidate = settings.segger_root / name
        available = candidate.is_file() and os.access(candidate, os.X_OK)
        tools.append(
            ToolAvailability(
                name=name,
                state=(
                    CapabilityState.AVAILABLE
                    if available
                    else CapabilityState.UNAVAILABLE
                ),
                path=str(candidate.resolve()) if available else None,
                version=segger_version if available else None,
                reason=None if available else f"not found below {settings.segger_root}",
            )
        )
    for name, configured in (
        ("gdb-client", settings.gdb_client),
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
                state=(CapabilityState.AVAILABLE if path else CapabilityState.UNAVAILABLE),
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


def capability_manifest(
    settings: Settings, targets: TargetRegistry | None = None
) -> CapabilityManifest:
    targets = targets or TargetRegistry()
    probes: list[ProbeCapabilities] = []
    boards = []
    for usb in discover_usb_devices():
        if usb.kind == "jlink" and usb.serial:
            compact_probe = usb.product_id == "1020"
            probes.append(
                ProbeCapabilities(
                    serial=usb.serial,
                    model=(
                        "SEGGER J-Link (EDU Mini V2 compatible USB identity)"
                        if compact_probe
                        else "SEGGER J-Link"
                    ),
                    usb=usb,
                    interfaces=["SWD"],
                    max_swd_speed_khz=4000 if compact_probe else None,
                    max_swo_speed_khz=4000 if compact_probe else None,
                    target_power=False if compact_probe else None,
                    trace=["SWO", "RTT"],
                )
            )
            continue
        if board := targets.detect_board(usb):
            boards.append(board)

    tools = discover_tools(settings)
    available = {tool.name for tool in tools if tool.state == CapabilityState.AVAILABLE}
    workflows = {
        "preflight": CapabilityState.AVAILABLE,
        "flash_verify": _tool_state(available, "JLinkExe"),
        "debug": _tools_state(available, {"JLinkGDBServerCLExe", "gdb-client"}),
        "gui": (
            _tools_state(available, {"Xvfb", "xdotool"})
            if settings.enable_gui
            else CapabilityState.UNAVAILABLE
        ),
        "sdk": CapabilityState.UNAVAILABLE,
        "serial": (
            CapabilityState.AVAILABLE
            if any(board.serial_port for board in boards)
            else CapabilityState.UNAVAILABLE
        ),
        "rtt": _tool_state(available, "JLinkRTTLoggerExe"),
        "swo_itm": (
            CapabilityState.AVAILABLE
            if any(probe.max_swo_speed_khz for probe in probes)
            else CapabilityState.UNAVAILABLE
        ),
        "backup_restore": _tool_state(available, "JLinkExe"),
        "firmware_comparison": _tool_state(available, "JLinkExe"),
        "rtt_capture": _tool_state(available, "JLinkRTTLoggerExe"),
        "validation_report": CapabilityState.AVAILABLE,
    }

    def detail(
        name: str, dependencies: list[str], unavailable_reason: str
    ) -> CapabilityAvailability:
        state = workflows[name]
        return CapabilityAvailability(
            state=state,
            dependencies=dependencies,
            reason=None if state == CapabilityState.AVAILABLE else unavailable_reason,
        )

    unique_pair = len(probes) == 1 and len(boards) == 1
    limitations = [
        "Probe capabilities are dynamically constrained by model, firmware, and licenses.",
        "Board identity inferred over USB must be confirmed by a registered target profile before writes.",
        "Direct J-Link SDK calls are unavailable until a licensed SDK is mounted.",
    ]
    if probes and probes[0].usb.product_id == "1020":
        limitations.append(
            "EDU Mini use is restricted to qualifying non-profit educational work."
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
            "flash_verify": detail(
                "flash_verify", ["JLinkExe"], "J-Link Commander is not installed"
            ),
            "debug": detail(
                "debug",
                ["JLinkGDBServerCLExe", "gdb-client"],
                "J-Link GDB Server and a configured GDB client are required",
            ),
            "gui": detail(
                "gui",
                ["Xvfb", "xdotool"],
                "GUI is disabled or Xvfb/xdotool is missing",
            ),
            "sdk": CapabilityAvailability(
                state=CapabilityState.UNAVAILABLE,
                dependencies=["licensed J-Link SDK mount"],
                reason="No separately licensed J-Link SDK package is mounted",
            ),
            "serial": detail(
                "serial",
                ["registered board serial channel"],
                "No registered board with an accessible serial node was discovered",
            ),
            "rtt": detail(
                "rtt", ["JLinkRTTLoggerExe"], "J-Link RTT Logger is not installed"
            ),
            "swo_itm": detail(
                "swo_itm",
                ["probe SWO support", "SWO wire"],
                "No discovered probe reports SWO support",
            ),
            "backup_restore": detail(
                "backup_restore", ["JLinkExe"], "J-Link Commander is not installed"
            ),
            "firmware_comparison": detail(
                "firmware_comparison", ["JLinkExe"], "J-Link Commander is not installed"
            ),
            "rtt_capture": detail(
                "rtt_capture",
                ["JLinkRTTLoggerExe", "ELF RTT symbol"],
                "RTT Logger or a concrete control-block symbol is unavailable",
            ),
            "validation_report": detail(
                "validation_report", ["persistent state"], "Persistent state is unavailable"
            ),
        },
        features={
            "target_power": CapabilityAvailability(
                state=(
                    CapabilityState.AVAILABLE
                    if any(probe.target_power for probe in probes)
                    else CapabilityState.UNAVAILABLE
                ),
                reason=(
                    None
                    if any(probe.target_power for probe in probes)
                    else "No discovered probe reports target-power support"
                ),
            ),
            "etm_trace": CapabilityAvailability(
                state=CapabilityState.UNKNOWN,
                reason="Trace capability depends on the selected probe and target wiring",
            ),
            "swo_wire": CapabilityAvailability(
                state=CapabilityState.UNKNOWN,
                dependencies=["physical SWO connection"],
                reason="SWO wiring cannot be inferred from USB discovery; validate by capture",
            ),
            "ozone": CapabilityAvailability(
                state=_tool_state(available, "Ozone"),
                reason=None if "Ozone" in available else "Optional Ozone package is not mounted",
            ),
            "systemview": CapabilityAvailability(
                state=_tool_state(available, "SystemView"),
                reason=(
                    None
                    if "SystemView" in available
                    else "Optional SystemView package is not mounted"
                ),
            ),
            "semihosting": CapabilityAvailability(
                state=workflows["debug"],
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
            "list_jlink_probes",
            "connect_target",
            "disconnect_target",
            "get_probe_information",
            "reset_target",
            "halt_target",
            "run_target",
            "step_target",
            "read_memory",
            "write_memory",
            "read_register",
            "write_register",
            "set_breakpoint",
            "clear_breakpoint",
            "set_watchpoint",
            "clear_watchpoint",
            "erase_flash",
            "verify_binary",
            "serial_exchange",
            "capture_serial",
            "swo_control",
            "start_gdb_session",
            "gdb_command",
            "capture_gdb_channel",
            "exchange_gdb_channel",
            "stop_gdb_session",
            "inspect_elf",
            "run_segger_application",
            "launch_segger_gui",
            "gui_session_info",
            "gui_keys",
            "gui_click",
            "gui_screenshot",
            "gui_ocr",
            "gui_accessibility_tree",
            "gui_image_match",
            "stop_segger_gui",
        ],
        limitations=limitations,
        unique_pair=unique_pair,
        selected_probe_serial=probes[0].serial if unique_pair else None,
        selected_board_serial=boards[0].serial if unique_pair else None,
    )


def _tool_state(available: set[str], name: str) -> CapabilityState:
    return (
        CapabilityState.AVAILABLE
        if name in available
        else CapabilityState.UNAVAILABLE
    )


def _tools_state(available: set[str], names: set[str]) -> CapabilityState:
    return (
        CapabilityState.AVAILABLE
        if names <= available
        else CapabilityState.UNAVAILABLE
    )


def current_groups() -> set[str]:
    group_ids = os.getgroups()
    return {grp.getgrgid(group_id).gr_name for group_id in group_ids}
