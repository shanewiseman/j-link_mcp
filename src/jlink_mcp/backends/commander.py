"""J-Link Commander batch backend."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from pathlib import Path

from ..config import Settings
from ..models import CommandResult, DeviceSelector, TargetState
from ..profiles import TargetRegistry
from ..runner import ProcessRunner
from ..security import validate_raw_commands
from .base import DebugBackend

_VTREF_RE = re.compile(r"VTref\s*=\s*([0-9.]+)\s*V", re.IGNORECASE)
_CORTEX_RE = re.compile(r"Cortex-[MRA][0-9+]*", re.IGNORECASE)
_SERIAL_RE = re.compile(r"(?:S/N|Serial number)[:\s]+([0-9]+)", re.IGNORECASE)
_FIRMWARE_RE = re.compile(r"Firmware:\s*(.+)")
_LICENSE_RE = re.compile(r"License\(s\):\s*(.+)", re.IGNORECASE)
_HARDWARE_RE = re.compile(r"Hardware version:\s*(.+)", re.IGNORECASE)
_CPUID_RE = re.compile(r"CPUID register:\s*(0x[0-9A-Fa-f]+)")
_DPIDR_RE = re.compile(r"DPIDR:\s*(0x[0-9A-Fa-f]+)")
_PC_RE = re.compile(r"\bPC\s*=\s*([0-9A-Fa-f]+)")
_MODE_RE = re.compile(r"IPSR\s*=\s*\d+\s*\(([^)]+)\)", re.IGNORECASE)
_MEMORY_RE = re.compile(
    r"^\s*([0-9A-Fa-f]{8,16})\s*=\s*((?:[0-9A-Fa-f]{2,16}\s*)+)$",
    re.MULTILINE,
)
_REGISTER_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*([0-9A-Fa-f]{8,16})(?:\s|,|$)",
    re.MULTILINE,
)
_BREAKPOINT_RE = re.compile(
    r"(?:breakpoint|BP).*?(?:handle|index|#)\s*[:=]?\s*(\d+)", re.IGNORECASE
)
_WATCHPOINT_RE = re.compile(
    r"(?:watchpoint|WP).*?(?:handle|index|#)\s*[:=]?\s*(\d+)", re.IGNORECASE
)
_COMMANDER_VERSION_RE = re.compile(r"J-Link Commander V([^\s(]+)")
_DLL_VERSION_RE = re.compile(r"DLL version V([^,\s]+)")


def parse_commander_output(output: str) -> dict[str, object]:
    parsed: dict[str, object] = {}
    if match := _VTREF_RE.search(output):
        parsed["target_voltage"] = float(match.group(1))
    if match := _CORTEX_RE.search(output):
        parsed["core"] = match.group(0)
    if match := _SERIAL_RE.search(output):
        parsed["probe_serial"] = match.group(1)
    if match := _FIRMWARE_RE.search(output):
        parsed["firmware"] = match.group(1).strip()
    if match := _HARDWARE_RE.search(output):
        parsed["hardware_version"] = match.group(1).strip()
    if match := _LICENSE_RE.search(output):
        parsed["licenses"] = [
            item.strip() for item in match.group(1).split(",") if item.strip()
        ]
    if match := _CPUID_RE.search(output):
        parsed["cpuid"] = match.group(1)
    if match := _DPIDR_RE.search(output):
        parsed["dpidr"] = match.group(1)
    if match := _PC_RE.search(output):
        parsed["pc"] = f"0x{match.group(1).upper()}"
    if match := _MODE_RE.search(output):
        parsed["exception"] = match.group(1)
    memory: list[dict[str, object]] = []
    for match in _MEMORY_RE.finditer(output):
        raw_words = match.group(2).split()
        memory.append(
            {
                "address": f"0x{int(match.group(1), 16):08X}",
                "values": [f"0x{int(word, 16):0{len(word)}X}" for word in raw_words],
            }
        )
    if memory:
        parsed["memory"] = memory
    registers = {
        match.group(1).upper(): f"0x{int(match.group(2), 16):0{len(match.group(2))}X}"
        for match in _REGISTER_RE.finditer(output)
        if match.group(1).upper() not in {"VTREF"}
    }
    if registers:
        parsed["registers"] = registers
    if match := _BREAKPOINT_RE.search(output):
        parsed["breakpoint_handle"] = int(match.group(1))
    if match := _WATCHPOINT_RE.search(output):
        parsed["watchpoint_handle"] = int(match.group(1))
    if match := _COMMANDER_VERSION_RE.search(output):
        parsed["commander_version"] = match.group(1)
    if match := _DLL_VERSION_RE.search(output):
        parsed["dll_version"] = match.group(1)
    parsed["flash_verified"] = bool(
        re.search(r"(?:verified\s+O\.K\.|Verify(?:Bin)?.*?O\.K\.)", output, re.IGNORECASE | re.DOTALL)
    )
    lowered = output.lower()
    if "cannot connect" in lowered or "could not connect" in lowered:
        parsed["connected"] = False
    elif "cortex-" in lowered or "found swd-dp" in lowered:
        parsed["connected"] = True
    if "pc =" in lowered or "currentpc" in lowered:
        parsed["registers_present"] = True
    return parsed


class CommanderBackend(DebugBackend):
    name = "jlink-commander"

    def __init__(
        self, settings: Settings, runner: ProcessRunner, targets: TargetRegistry
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.targets = targets

    def _command_file(self, commands: Sequence[str]) -> Path:
        command_dir = self.settings.state_root / "commands"
        command_dir.mkdir(parents=True, exist_ok=True)
        path = command_dir / f"{uuid.uuid4()}.jlink"
        path.write_text("\n".join([*commands, "Exit"]) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    async def execute(
        self,
        commands: Sequence[str],
        *,
        selector: DeviceSelector | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        validated = validate_raw_commands(commands, settings=self.settings)
        command_file = self._command_file(validated)
        executable = self.settings.segger_executable("JLinkExe")
        argv: list[str | Path] = [
            executable,
            "-NoGui",
            "1",
            "-ExitOnError",
            "1",
            "-CommandFile",
            command_file,
        ]
        if selector:
            if selector.probe_serial:
                argv.extend(["-USB", selector.probe_serial])
            argv.extend(
                [
                    "-Device",
                    self.targets.jlink_device(
                        selector.target_profile, selector.core
                    ),
                    "-If",
                    selector.interface,
                    "-Speed",
                    str(selector.speed_khz),
                    "-AutoConnect",
                    "1",
                ]
            )
        else:
            argv.extend(["-AutoConnect", "0"])
        state_before, state_after = _infer_states(validated)
        result = await self.runner.run(
            argv,
            backend=self.name,
            cwd=self.settings.workspace_root,
            timeout=timeout or self.settings.default_timeout_seconds,
            state_before=state_before,
            state_after=state_after,
        )
        result.parsed = parse_commander_output(result.stdout + "\n" + result.stderr)
        result.evidence_paths.append(str(command_file))
        return result

    async def probe_list(self, *, timeout: float = 15) -> CommandResult:
        return await self.execute(["ShowEmuList USB"], timeout=timeout)


def _infer_states(commands: Sequence[str]) -> tuple[TargetState, TargetState]:
    after = TargetState.UNKNOWN
    for command in commands:
        name = command.split(maxsplit=1)[0].lower()
        if name in {"h", "halt"}:
            after = TargetState.HALTED
        elif name in {"g", "go"}:
            after = TargetState.RUNNING
        elif name in {"r", "reset"}:
            after = TargetState.RESET
        elif name in {"s", "step"}:
            after = TargetState.HALTED
    return TargetState.UNKNOWN, after
