"""Target-neutral dependency and permission preflight."""

from __future__ import annotations

import grp
import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path

from .config import Settings
from .discovery import capability_manifest, current_groups
from .models import CapabilityState, DependencyCheck, DependencyReport
from .profiles import TargetRegistry


def dependency_report(
    settings: Settings, targets: TargetRegistry | None = None
) -> DependencyReport:
    manifest = capability_manifest(settings, targets)
    tool_map = {tool.name: tool for tool in manifest.tools}
    groups = current_groups()
    in_container = Path("/.dockerenv").exists()
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
            ok=in_container
            or _command_ok(["docker", "version", "--format", "{{.Server.Version}}"]),
            required=not in_container,
            observed="container runtime" if in_container else shutil.which("docker"),
            expected="working Docker Engine",
        ),
        DependencyCheck(
            name="docker-compose",
            ok=in_container or _command_ok(["docker", "compose", "version"]),
            required=not in_container,
            observed=(
                "host concern"
                if in_container
                else _command_output(["docker", "compose", "version"])
            ),
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
            ok=tool_ok(tool_map, "JLinkExe"),
            observed=tool_path(tool_map, "JLinkExe"),
            expected="SEGGER JLinkExe",
        ),
        DependencyCheck(
            name="segger-version-9.62",
            ok=tool_version(tool_map, "JLinkExe") == "9.62",
            observed=tool_version(tool_map, "JLinkExe"),
            expected="9.62",
        ),
        DependencyCheck(
            name="jlink-gdb-server",
            ok=tool_ok(tool_map, "JLinkGDBServerCLExe"),
            observed=tool_path(tool_map, "JLinkGDBServerCLExe"),
            expected="SEGGER JLinkGDBServerCLExe",
        ),
        DependencyCheck(
            name="gdb-client",
            ok=tool_ok(tool_map, "gdb-client"),
            observed=tool_path(tool_map, "gdb-client"),
            expected="configured GDB client",
        ),
        DependencyCheck(
            name="workspace",
            ok=directory_access(settings.workspace_root, write=True),
            observed=str(settings.workspace_root),
            expected="read/write workspace mount",
        ),
        DependencyCheck(
            name="state",
            ok=directory_access(settings.state_root, write=True),
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
            name="unique-probe",
            ok=len(manifest.probes) == 1,
            observed=f"{len(manifest.probes)} probe(s)",
            expected="one J-Link or an explicit stable selector",
            remediation="Specify probe_serial when more than one J-Link is attached.",
        ),
        DependencyCheck(
            name="plugdev-group",
            ok="plugdev" in groups or os.geteuid() == 0,
            observed=",".join(sorted(groups)),
            expected="plugdev membership",
        ),
        DependencyCheck(
            name="group-device-modes",
            ok=device_modes_ok(manifest, settings),
            observed=device_mode_summary(manifest, settings),
            expected="0660 plugdev/dialout device ownership",
            remediation="Install the applicable probe and extension udev rules, then reconnect devices.",
        ),
        DependencyCheck(
            name="xvfb",
            ok=tool_ok(tool_map, "Xvfb"),
            required=settings.enable_gui,
            observed=tool_path(tool_map, "Xvfb"),
            expected="Xvfb for isolated GUI automation",
        ),
        DependencyCheck(
            name="xdotool",
            ok=tool_ok(tool_map, "xdotool"),
            required=settings.enable_gui,
            observed=tool_path(tool_map, "xdotool"),
            expected="xdotool for GUI automation",
        ),
    ]
    return DependencyReport(checks=checks, manifest=manifest)


def _filesystem_type(path: Path) -> str:
    try:
        if (
            path == Path("/sys/fs/cgroup")
            and os.statvfs(path).f_fsid
            and Path("/sys/fs/cgroup/cgroup.controllers").exists()
        ):
            return "cgroup2fs"
    except OSError:
        return "missing"
    return "unknown"


def _command_ok(argv: list[str]) -> bool:
    try:
        return (
            subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            ).returncode
            == 0
        )
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


def directory_access(path: Path, *, write: bool) -> bool:
    mode = os.R_OK | (os.W_OK if write else 0)
    return path.is_dir() and os.access(path, mode)


def tool_ok(tools: dict[str, object], name: str) -> bool:
    tool = tools.get(name)
    return bool(tool and getattr(tool, "state", None) == CapabilityState.AVAILABLE)


def tool_path(tools: dict[str, object], name: str) -> str | None:
    tool = tools.get(name)
    return getattr(tool, "path", None) if tool else None


def tool_version(tools: dict[str, object], name: str) -> str | None:
    tool = tools.get(name)
    return getattr(tool, "version", None) if tool else None


def device_access_entries(manifest: object, settings: Settings) -> list[tuple[Path, str]]:
    entries: dict[Path, str] = {}
    for probe in manifest.probes:
        if probe.usb:
            for node in probe.usb.device_nodes:
                entries[host_visible_device(Path(node), settings)] = "plugdev"
    for board in manifest.boards:
        if board.usb:
            for node in board.usb.device_nodes:
                path = host_visible_device(Path(node), settings)
                expected_group = (
                    "dialout"
                    if Path(node).name.startswith(("ttyACM", "ttyUSB"))
                    else "plugdev"
                )
                entries[path] = expected_group
        if board.serial_port:
            entries[host_visible_device(Path(board.serial_port), settings)] = "dialout"
    return sorted(entries.items(), key=lambda item: str(item[0]))


def host_visible_device(path: Path, settings: Settings) -> Path:
    if path.exists() or not path.is_absolute() or path.parts[1:2] != ("dev",):
        return path
    return settings.host_dev_root / path.relative_to("/dev")


def _device_group(path: Path) -> str:
    try:
        return grp.getgrgid(path.stat().st_gid).gr_name
    except (KeyError, OSError):
        return "unknown"


def device_modes_ok(manifest: object, settings: Settings) -> bool:
    entries = device_access_entries(manifest, settings)
    return bool(entries) and all(
        path.exists()
        and stat.S_IMODE(path.stat().st_mode) == 0o660
        and _device_group(path) == expected_group
        for path, expected_group in entries
    )


def device_mode_summary(manifest: object, settings: Settings) -> str:
    summaries: list[str] = []
    for path, expected_group in device_access_entries(manifest, settings):
        if path.exists():
            summaries.append(
                f"{path}:{stat.S_IMODE(path.stat().st_mode):04o}:"
                f"{_device_group(path)} (expected {expected_group})"
            )
        else:
            summaries.append(f"{path}:missing (expected 0660:{expected_group})")
    return ", ".join(summaries)
