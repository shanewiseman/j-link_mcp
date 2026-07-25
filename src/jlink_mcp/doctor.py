"""Dependency and permission preflight."""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import grp
from pathlib import Path

from .config import Settings
from .discovery import capability_manifest, current_groups
from .models import CapabilityState, DependencyCheck, DependencyReport


def dependency_report(settings: Settings) -> DependencyReport:
    manifest = capability_manifest(settings)
    tool_map = {tool.name: tool for tool in manifest.tools}
    groups = current_groups()
    in_container = Path("/.dockerenv").exists()
    platform_root = (
        settings.arduino_data_root
        / "packages/arduino/hardware/mbed_giga/4.6.0"
    )
    bridge_libraries = {
        "Arduino_USBHostMbed5": "0.3.1",
        "ArduinoBLE": "2.1.0",
        "Arduino_SpiNINA": "0.0.2",
    }
    checks = [
        DependencyCheck(
            name="linux",
            ok=platform.system() == "Linux",
            observed=platform.system(),
            expected="Linux",
        ),
        DependencyCheck(
            name="x86_64",
            ok=platform.machine() in {"x86_64", "amd64"},
            observed=platform.machine(),
            expected="x86_64",
        ),
        DependencyCheck(
            name="cgroup-v2",
            ok=_filesystem_type(Path("/sys/fs/cgroup")) == "cgroup2fs",
            observed=_filesystem_type(Path("/sys/fs/cgroup")),
            expected="cgroup2fs",
            remediation="Enable cgroup v2 for restricted hot-plug device rules.",
        ),
        DependencyCheck(
            name="docker-engine",
            ok=in_container or _command_ok(["docker", "version", "--format", "{{.Server.Version}}"]),
            required=not in_container,
            observed="container runtime" if in_container else shutil.which("docker"),
            expected="working Docker Engine",
        ),
        DependencyCheck(
            name="docker-compose",
            ok=in_container or _command_ok(["docker", "compose", "version"]),
            required=not in_container,
            observed="host concern" if in_container else _command_output(["docker", "compose", "version"]),
            expected="Docker Compose v2+",
        ),
        DependencyCheck(
            name="bearer-token",
            ok=bool(settings.bearer_token(required=False)),
            observed=str(settings.token_file) if settings.token_file else "environment",
            expected="non-empty JLINK_MCP_TOKEN or mode-0600 token file",
            remediation="Run scripts/bootstrap.sh or jlink-mcp token --output .token.",
        ),
        DependencyCheck(
            name="segger-root",
            ok=settings.segger_root.is_dir(),
            observed=str(settings.segger_root),
            expected="mounted J-Link Software Pack directory",
        ),
        DependencyCheck(
            name="jlink-commander",
            ok=_tool_ok(tool_map, "JLinkExe"),
            observed=_tool_path(tool_map, "JLinkExe"),
            expected="SEGGER JLinkExe",
        ),
        DependencyCheck(
            name="segger-version-9.62",
            ok=_tool_version(tool_map, "JLinkExe") == "9.62",
            observed=_tool_version(tool_map, "JLinkExe"),
            expected="9.62",
        ),
        DependencyCheck(
            name="jlink-gdb-server",
            ok=_tool_ok(tool_map, "JLinkGDBServerCLExe"),
            observed=_tool_path(tool_map, "JLinkGDBServerCLExe"),
            expected="SEGGER JLinkGDBServerCLExe",
        ),
        DependencyCheck(
            name="arduino-cli",
            ok=_tool_ok(tool_map, "arduino-cli"),
            observed=_tool_path(tool_map, "arduino-cli"),
            expected="arduino-cli 1.5.1",
        ),
        DependencyCheck(
            name="arm-gdb",
            ok=_tool_ok(tool_map, "arm-none-eabi-gdb"),
            observed=_tool_path(tool_map, "arm-none-eabi-gdb"),
            expected="Arm GDB supplied by Arduino GIGA platform",
        ),
        *[
            DependencyCheck(
                name=name,
                ok=_tool_ok(tool_map, name),
                observed=_tool_path(tool_map, name),
                expected=f"pinned Arduino GIGA {name}",
            )
            for name in (
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
        *[
            DependencyCheck(
                name=f"arduino-library-{name.lower()}-{version}",
                ok=_arduino_library_version(settings.arduino_user_root, name) == version,
                observed=_arduino_library_version(settings.arduino_user_root, name),
                expected=f"{name}@{version}",
                remediation="Rebuild the pinned container image.",
            )
            for name, version in bridge_libraries.items()
        ],
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
            name="workspace",
            ok=_directory_access(settings.workspace_root, write=True),
            observed=str(settings.workspace_root),
            expected="read/write workspace mount",
        ),
        DependencyCheck(
            name="state",
            ok=_directory_access(settings.state_root, write=True),
            observed=str(settings.state_root),
            expected="read/write persistent state volume",
        ),
        DependencyCheck(
            name="jlink-attached",
            ok=len(manifest.probes) >= 1,
            observed=str([probe.serial for probe in manifest.probes]),
            expected="at least one unique J-Link",
        ),
        DependencyCheck(
            name="giga-attached",
            ok=len(manifest.boards) >= 1,
            observed=str([board.serial for board in manifest.boards]),
            expected="Arduino GIGA R1 USB identity",
        ),
        DependencyCheck(
            name="unique-pair",
            ok=manifest.unique_pair,
            observed=f"{len(manifest.probes)} probe(s), {len(manifest.boards)} board(s)",
            expected="one J-Link and one GIGA",
            remediation="Specify stable serial selectors when more than one device exists.",
        ),
        DependencyCheck(
            name="plugdev-group",
            ok="plugdev" in groups or os.geteuid() == 0,
            observed=",".join(sorted(groups)),
            expected="plugdev membership",
        ),
        DependencyCheck(
            name="dialout-group",
            ok="dialout" in groups or os.geteuid() == 0,
            observed=",".join(sorted(groups)),
            expected="dialout membership or accessible ttyACM device",
        ),
        DependencyCheck(
            name="group-device-modes",
            ok=_device_modes_ok(manifest, settings),
            observed=_device_mode_summary(manifest, settings),
            expected="0660 plugdev/dialout udev ownership",
            remediation="Run scripts/install-udev-rules.sh once, then reconnect devices.",
        ),
        DependencyCheck(
            name="xvfb",
            ok=_tool_ok(tool_map, "Xvfb"),
            required=settings.enable_gui,
            observed=_tool_path(tool_map, "Xvfb"),
            expected="Xvfb for isolated GUI automation",
        ),
        DependencyCheck(
            name="xdotool",
            ok=_tool_ok(tool_map, "xdotool"),
            required=settings.enable_gui,
            observed=_tool_path(tool_map, "xdotool"),
            expected="xdotool for GUI automation",
        ),
    ]
    return DependencyReport(checks=checks, manifest=manifest)


def _filesystem_type(path: Path) -> str:
    try:
        return os.statvfs(path).f_fsid and "cgroup2fs" if path == Path("/sys/fs/cgroup") and Path("/sys/fs/cgroup/cgroup.controllers").exists() else "unknown"
    except OSError:
        return "missing"


def _command_ok(argv: list[str]) -> bool:
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _command_output(argv: list[str]) -> str | None:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _directory_access(path: Path, *, write: bool) -> bool:
    mode = os.R_OK | (os.W_OK if write else 0)
    return path.is_dir() and os.access(path, mode)


def _tool_ok(tools: dict[str, object], name: str) -> bool:
    tool = tools.get(name)
    return bool(tool and getattr(tool, "state", None) == CapabilityState.AVAILABLE)


def _tool_path(tools: dict[str, object], name: str) -> str | None:
    tool = tools.get(name)
    return getattr(tool, "path", None) if tool else None


def _tool_version(tools: dict[str, object], name: str) -> str | None:
    tool = tools.get(name)
    return getattr(tool, "version", None) if tool else None


def _arduino_library_version(root: Path, name: str) -> str | None:
    properties = root / "libraries" / name / "library.properties"
    try:
        for line in properties.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "version":
                return value.strip()
    except OSError:
        return None
    return None


def _device_access_entries(manifest: object, settings: Settings) -> list[tuple[Path, str]]:
    entries: dict[Path, str] = {}
    for probe in manifest.probes:
        if not probe.usb:
            continue
        for node in probe.usb.device_nodes:
            entries[_host_visible_device(Path(node), settings)] = "plugdev"
    for board in manifest.boards:
        if board.usb:
            for node in board.usb.device_nodes:
                path = _host_visible_device(Path(node), settings)
                expected_group = (
                    "dialout"
                    if Path(node).name.startswith(("ttyACM", "ttyUSB"))
                    else "plugdev"
                )
                entries[path] = expected_group
        if board.serial_port:
            entries[_host_visible_device(Path(board.serial_port), settings)] = "dialout"
    return sorted(entries.items(), key=lambda item: str(item[0]))


def _host_visible_device(path: Path, settings: Settings) -> Path:
    if path.exists() or not path.is_absolute() or not path.parts[1:2] == ("dev",):
        return path
    return settings.host_dev_root / path.relative_to("/dev")


def _device_group(path: Path) -> str:
    try:
        return grp.getgrgid(path.stat().st_gid).gr_name
    except (KeyError, OSError):
        return "unknown"


def _device_modes_ok(manifest: object, settings: Settings) -> bool:
    entries = _device_access_entries(manifest, settings)
    return bool(entries) and all(
        path.exists()
        and stat.S_IMODE(path.stat().st_mode) == 0o660
        and _device_group(path) == expected_group
        for path, expected_group in entries
    )


def _device_mode_summary(manifest: object, settings: Settings) -> str:
    summaries: list[str] = []
    for path, expected_group in _device_access_entries(manifest, settings):
        if path.exists():
            summaries.append(
                f"{path}:{stat.S_IMODE(path.stat().st_mode):04o}:"
                f"{_device_group(path)} (expected {expected_group})"
            )
        else:
            summaries.append(f"{path}:missing (expected 0660:{expected_group})")
    return ", ".join(summaries)


def _serial_world_rw() -> bool:
    return any(os.access(path, os.R_OK | os.W_OK) for path in Path("/dev").glob("ttyACM*"))
