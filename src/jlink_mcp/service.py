"""Application service coordinating discovery, leases, backends, and audit."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import inspect_elf
from .backends import (
    ApplicationBackend,
    CommanderBackend,
    GDBBackend,
    GUIBackend,
    SDKBackend,
    SerialBackend,
)
from .config import Settings
from .discovery import capability_manifest
from .doctor import dependency_report
from .extensions.api import ExtensionRegistry
from .leases import ProbeLeaseManager
from .models import (
    CapabilityManifest,
    CommandResult,
    DependencyCheck,
    DependencyReport,
    DeviceSelector,
)
from .runner import ProcessRunner
from .store import AuditStore


class TargetSelectionError(RuntimeError):
    pass


_READ_ONLY_COMMANDER_COMMANDS = {
    "mem",
    "mem8",
    "mem16",
    "mem32",
    "rreg",
    "regs",
    "savebin",
    "showconf",
    "showemulist",
    "showfwinfo",
    "showhwstatus",
    "sworead",
    "swospeed",
    "swostat",
    "uptime",
    "verifybin",
}
_INFORMATIONAL_APPLICATION_ARGUMENTS = {
    "-?",
    "-h",
    "--help",
    "-help",
    "--version",
    "-version",
}


class JLinkService:
    def __init__(
        self, settings: Settings, registry: ExtensionRegistry | None = None
    ) -> None:
        self.settings = settings
        self.extensions = registry or ExtensionRegistry()
        self.settings.ensure_directories()
        self.runner = ProcessRunner(max_output_bytes=settings.max_output_bytes)
        self.store = AuditStore(settings.state_root / "jlink-mcp.sqlite3")
        stale_sessions = self.store.clear_stale_sessions()
        self.leases = ProbeLeaseManager()
        self.commander = CommanderBackend(
            settings, self.runner, self.extensions.targets
        )
        self.gdb = GDBBackend(settings, self.runner, self.extensions.targets)
        self.gui = GUIBackend(settings, self.runner)
        self.application = ApplicationBackend(settings, self.runner)
        self.serial = SerialBackend()
        self.sdk = SDKBackend()
        self._gdb_leases: dict[str, str] = {}
        self._gdb_selectors: dict[str, DeviceSelector] = {}
        self._gdb_identities: dict[str, dict[str, Any]] = {}
        self._gui_leases: dict[str, str] = {}
        self._gui_selectors: dict[str, DeviceSelector] = {}
        if stale_sessions:
            now = datetime.now(UTC)
            recovery = CommandResult(
                operation_id=str(uuid.uuid4()),
                backend="session-recovery",
                command=["clear-stale-sessions"],
                started_at=now,
                finished_at=now,
                duration_ms=0,
                return_code=0,
                parsed={"recovered": stale_sessions},
            )
            self.store.append_operation(
                result=recovery,
                action="recover_stale_sessions",
                probe_serial=None,
                destructive=False,
            )

    def capabilities(self) -> CapabilityManifest:
        manifest = capability_manifest(self.settings, self.extensions.targets)
        # USB discovery cannot report probe firmware or licensed features.
        # Enrich it from the newest hash-chained positive identity observation.
        observations: dict[str, dict[str, Any]] = {}
        for entry in self.store.list_operations(limit=1000):
            result = entry["payload"].get("result", {})
            probe = result.get("probe_identity", {})
            serial = probe.get("serial")
            if not serial:
                continue
            observation = observations.setdefault(str(serial), {})
            for key in ("observed_serial", "firmware", "hardware_version"):
                if not observation.get(key) and probe.get(key):
                    observation[key] = probe[key]
            if not observation.get("licenses") and probe.get("licenses"):
                observation["licenses"] = list(probe["licenses"])
        for probe in manifest.probes:
            observation = observations.get(probe.serial)
            if not observation:
                continue
            probe.firmware = observation.get("firmware")
            probe.licenses = list(observation.get("licenses") or [])
            if probe.licenses:
                probe.model = (
                    observation.get("hardware_version") and probe.model
                ) or probe.model
        return self.extensions.merge_capabilities(manifest)

    def doctor(self) -> DependencyReport:
        report = dependency_report(self.settings, self.extensions.targets)
        report.manifest = self.capabilities()
        probe_evidence: dict[str, Any] = {}
        for entry in self.store.list_operations(limit=1000):
            result = entry["payload"].get("result", {})
            probe = result.get("probe_identity", {})
            if (
                probe.get("licenses")
                and not probe_evidence.get("licenses")
                or not probe_evidence
                and probe.get("serial")
            ):
                probe_evidence = probe
        report.checks.extend(self.extensions.dependency_checks(report.manifest))
        report.checks.extend(
            [
                DependencyCheck(
                    name="probe-licenses",
                    ok=bool(probe_evidence.get("licenses")),
                    observed=str(probe_evidence.get("licenses") or "not observed"),
                    expected="live license list from J-Link Commander",
                    remediation="Run get_probe_information for the selected probe.",
                ),
                DependencyCheck(
                    name="container-non-root",
                    ok=os.geteuid() != 0,
                    observed=f"uid={os.geteuid()}",
                    expected="non-root service user",
                ),
                DependencyCheck(
                    name="container-capabilities-dropped",
                    ok=_effective_capabilities() == 0,
                    observed=f"CapEff=0x{_effective_capabilities():x}",
                    expected="CapEff=0",
                ),
            ]
        )
        return report

    def resolve_selector(self, selector: DeviceSelector | None) -> DeviceSelector:
        manifest = self.capabilities()
        selector = selector or DeviceSelector()
        profiles = self.extensions.targets.profiles
        if not profiles:
            raise TargetSelectionError(
                "no target profile is registered; enable an extension that "
                "provides the selected target"
            )

        profile_id = selector.target_profile
        if profile_id is None:
            matching_profile_ids = {
                board.target_profile
                for board in manifest.boards
                if board.target_profile
                and (
                    selector.board_serial is None
                    or board.serial == selector.board_serial
                )
            }
            if len(matching_profile_ids) == 1:
                profile_id = matching_profile_ids.pop()
            elif len(profiles) == 1:
                profile_id = next(iter(profiles))
            else:
                raise TargetSelectionError(
                    "target profile selection is ambiguous; provide target_profile"
                )
        try:
            profile = self.extensions.targets.get_profile(profile_id)
        except ValueError as exc:
            raise TargetSelectionError(str(exc)) from exc
        core = selector.core or profile.default_core
        if core not in profile.cores:
            raise TargetSelectionError(
                f"unknown core {core!r} for target profile {profile_id}"
            )

        probe_serial = selector.probe_serial
        board_serial = selector.board_serial

        if probe_serial is None:
            if len(manifest.probes) != 1:
                raise TargetSelectionError(
                    "probe selection is ambiguous; provide probe_serial"
                )
            probe_serial = manifest.probes[0].serial
        if probe_serial not in {probe.serial for probe in manifest.probes}:
            raise TargetSelectionError(f"J-Link serial is not attached: {probe_serial}")

        applicable_boards = [
            board for board in manifest.boards if board.target_profile == profile_id
        ]
        if board_serial is None and len(applicable_boards) == 1:
            board_serial = applicable_boards[0].serial
        elif board_serial and board_serial not in {
            board.serial for board in applicable_boards
        }:
            if manifest.boards or not self.store.has_verified_target(
                board_serial, probe_serial
            ):
                raise TargetSelectionError(
                    f"board serial is not attached or audit-verified: {board_serial}"
                )
        elif len(applicable_boards) > 1 and board_serial is None:
            raise TargetSelectionError(
                "board selection is ambiguous; provide board_serial"
            )

        updates = {
            "probe_serial": probe_serial,
            "board_serial": board_serial,
            "target_profile": profile_id,
            "core": core,
        }
        if "interface" not in selector.model_fields_set:
            updates["interface"] = profile.default_interface
        if "speed_khz" not in selector.model_fields_set:
            updates["speed_khz"] = profile.default_speed_khz
        return selector.model_copy(update=updates)

    async def resolve_selector_wait(
        self,
        selector: DeviceSelector | None,
        *,
        timeout: float = 10.0,
    ) -> DeviceSelector:
        """Wait for stable USB serial identities across reset renumbering."""

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                return self.resolve_selector(selector)
            except TargetSelectionError:
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                await asyncio.sleep(0.2)

    async def commander_commands(
        self,
        commands: Sequence[str],
        *,
        selector: DeviceSelector | None,
        action: str,
        destructive: bool,
        timeout: float | None = None,
    ) -> CommandResult:
        resolved = await self.resolve_selector_wait(selector)
        assert resolved.probe_serial is not None
        async with self.leases.lease(
            resolved.probe_serial,
            owner=action,
            timeout=timeout or self.settings.default_timeout_seconds,
        ) as lease:
            if action == "connect":
                result = await self.commander.execute(
                    commands, selector=resolved, timeout=timeout
                )
                result.session_id = lease.lease_id
                result.parsed.setdefault("selector", resolved.model_dump(mode="json"))
                self._attach_identities(result, resolved, result.parsed)
                self.store.append_operation(
                    result=result,
                    action=action,
                    probe_serial=resolved.probe_serial,
                    destructive=destructive,
                    request={
                        "selector": resolved.model_dump(mode="json"),
                        "commands": list(commands),
                    },
                )
                self._validate_identity(result, resolved)
                return result
            else:
                identity = await self._identity_preflight(resolved, lease.lease_id)
                result = await self.commander.execute(
                    commands, selector=resolved, timeout=timeout
                )
            self._validate_identity(identity, resolved)
            result.session_id = lease.lease_id
            identity_data = dict(identity.parsed)
        result.parsed.setdefault("selector", resolved.model_dump(mode="json"))
        result.parsed.setdefault("identity_preflight", identity_data)
        self._attach_identities(result, resolved, identity_data)
        self.store.append_operation(
            result=result,
            action=action,
            probe_serial=resolved.probe_serial,
            destructive=destructive,
            request={
                "selector": resolved.model_dump(mode="json"),
                "commands": list(commands),
            },
        )
        return result

    async def _identity_preflight(
        self, resolved: DeviceSelector, lease_id: str
    ) -> CommandResult:
        identity = await self.commander.execute(
            ["ShowHWStatus", "Mem32 0xE000ED00 1"],
            selector=resolved,
            timeout=self.settings.default_timeout_seconds,
        )
        identity.parsed.setdefault("selector", resolved.model_dump(mode="json"))
        identity.session_id = lease_id
        self._attach_identities(identity, resolved, identity.parsed)
        self.store.append_operation(
            result=identity,
            action="target_identity_preflight",
            probe_serial=resolved.probe_serial,
            destructive=False,
            request={"selector": resolved.model_dump(mode="json")},
        )
        self._validate_identity(identity, resolved)
        return identity

    @staticmethod
    def _serial_equal(observed: str, expected: str) -> bool:
        return observed.lstrip("0") == expected.lstrip("0")

    def _validate_identity(
        self, result: CommandResult, selector: DeviceSelector
    ) -> None:
        if selector.target_profile is None or selector.core is None:
            raise TargetSelectionError("target profile and core were not resolved")
        profile = self.extensions.targets.get_profile(selector.target_profile)
        expected = profile.cores[selector.core]
        parsed = result.parsed
        failures: list[str] = []
        if not result.ok or not parsed.get("connected"):
            failures.append("target did not connect")
        if parsed.get("core") != expected.expected_core:
            failures.append(
                f"core {parsed.get('core')!r} != {expected.expected_core!r}"
            )
        try:
            cpuid = int(str(parsed.get("cpuid", "")), 0)
        except ValueError:
            cpuid = -1
        if cpuid != expected.expected_cpuid:
            failures.append(
                f"CPUID {parsed.get('cpuid')!r} != 0x{expected.expected_cpuid:08X}"
            )
        try:
            dpidr = int(str(parsed.get("dpidr", "")), 0)
        except ValueError:
            dpidr = -1
        if dpidr != profile.expected_dpidr:
            failures.append(f"unexpected SW-DP ID {parsed.get('dpidr')!r}")
        voltage = parsed.get("target_voltage")
        if (
            not isinstance(voltage, (int, float))
            or voltage < profile.minimum_target_voltage
        ):
            failures.append(f"unsafe or missing target voltage {voltage!r}")
        probe = str(parsed.get("probe_serial", ""))
        if not selector.probe_serial or not self._serial_equal(
            probe, selector.probe_serial
        ):
            failures.append(f"probe serial {probe!r} != {selector.probe_serial!r}")
        if failures:
            raise TargetSelectionError(
                "positive target identification failed: " + "; ".join(failures)
            )

    @staticmethod
    def _attach_identities(
        result: CommandResult,
        selector: DeviceSelector,
        identity: dict[str, Any],
    ) -> None:
        result.probe_identity = {
            "serial": selector.probe_serial,
            "observed_serial": identity.get("probe_serial"),
            "firmware": identity.get("firmware"),
            "hardware_version": identity.get("hardware_version"),
            "licenses": identity.get("licenses", []),
        }
        result.target_identity = {
            "board_serial": selector.board_serial,
            "target_profile": selector.target_profile,
            "core": selector.core,
            "observed_core": identity.get("core"),
            "cpuid": identity.get("cpuid"),
            "dpidr": identity.get("dpidr"),
            "target_voltage": identity.get("target_voltage"),
        }

    async def probe_list(self) -> CommandResult:
        result = await self.commander.probe_list()
        self.store.append_operation(
            result=result,
            action="probe_list",
            probe_serial=None,
            destructive=False,
        )
        return result

    async def connect(self, selector: DeviceSelector | None = None) -> CommandResult:
        return await self.commander_commands(
            ["ShowHWStatus", "Mem32 0xE000ED00 1"],
            selector=selector,
            action="connect",
            destructive=False,
        )

    async def disconnect(self, selector: DeviceSelector | None = None) -> CommandResult:
        """Return the explicit state of Commander's session-scoped connection."""

        resolved = await self.resolve_selector_wait(selector)
        now = datetime.now(UTC)
        result = CommandResult(
            operation_id=str(uuid.uuid4()),
            backend="session",
            command=["disconnect"],
            started_at=now,
            finished_at=now,
            duration_ms=0,
            return_code=0,
            parsed={
                "disconnected": True,
                "detail": "Commander sessions disconnect after every command file",
                "selector": resolved.model_dump(mode="json"),
            },
        )
        self.store.append_operation(
            result=result,
            action="disconnect",
            probe_serial=resolved.probe_serial,
            destructive=False,
        )
        return result

    async def reset(
        self,
        selector: DeviceSelector | None = None,
        *,
        halt: bool = False,
        reset_type: int | None = None,
    ) -> CommandResult:
        if reset_type is not None and not 0 <= reset_type <= 15:
            raise ValueError("reset_type must be between 0 and 15")
        commands = [] if reset_type is None else [f"RSetType {reset_type}"]
        commands.extend(["Reset", "Halt"] if halt else ["Reset", "Go"])
        return await self.commander_commands(
            commands,
            selector=selector,
            action="reset_halt" if halt else "reset_run",
            destructive=True,
        )

    async def halt(self, selector: DeviceSelector | None = None) -> CommandResult:
        return await self.commander_commands(
            ["Halt", "Regs"], selector=selector, action="halt", destructive=True
        )

    async def go(self, selector: DeviceSelector | None = None) -> CommandResult:
        return await self.commander_commands(
            ["Go"], selector=selector, action="go", destructive=True
        )

    async def step(
        self, selector: DeviceSelector | None = None, *, count: int = 1
    ) -> CommandResult:
        if not 1 <= count <= 10000:
            raise ValueError("count must be between 1 and 10000")
        return await self.commander_commands(
            ["Halt", *("Step" for _ in range(count)), "Regs"],
            selector=selector,
            action="step",
            destructive=True,
            timeout=max(self.settings.default_timeout_seconds, count * 0.1),
        )

    async def read_memory(
        self,
        address: int,
        *,
        count: int = 1,
        width: int = 32,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        if address < 0 or count < 1 or count > 65536:
            raise ValueError("invalid address or count")
        if width not in {8, 16, 32}:
            raise ValueError("width must be 8, 16, or 32")
        return await self.commander_commands(
            [f"Mem{width} 0x{address:08X} {count}"],
            selector=selector,
            action="read_memory",
            destructive=False,
        )

    async def write_memory(
        self,
        address: int,
        values: list[int],
        *,
        width: int = 32,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        if address < 0 or not values or len(values) > 4096:
            raise ValueError("invalid address or values")
        if width not in {8, 16, 32}:
            raise ValueError("width must be 8, 16, or 32")
        maximum = (1 << width) - 1
        if any(value < 0 or value > maximum for value in values):
            raise ValueError(f"values must fit in {width} bits")
        command = f"W{width // 8} 0x{address:08X} " + " ".join(
            f"0x{value:X}" for value in values
        )
        return await self.commander_commands(
            [command],
            selector=selector,
            action="write_memory",
            destructive=True,
        )

    async def set_breakpoint(
        self, address: int, *, selector: DeviceSelector | None = None
    ) -> CommandResult:
        return await self.commander_commands(
            [f"SetBP 0x{address:08X}"],
            selector=selector,
            action="set_breakpoint",
            destructive=True,
        )

    async def read_register(
        self, name: str, *, selector: DeviceSelector | None = None
    ) -> CommandResult:
        if not name.replace("_", "").isalnum() or len(name) > 32:
            raise ValueError("invalid register name")
        return await self.commander_commands(
            [f"RReg {name}"],
            selector=selector,
            action="read_register",
            destructive=False,
        )

    async def write_register(
        self,
        name: str,
        value: int,
        *,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        if not name.replace("_", "").isalnum() or len(name) > 32:
            raise ValueError("invalid register name")
        if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
            raise ValueError("register value is out of range")
        return await self.commander_commands(
            [f"WReg {name}, 0x{value:X}"],
            selector=selector,
            action="write_register",
            destructive=True,
        )

    async def set_watchpoint(
        self,
        address: int,
        access: str = "W",
        *,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        access = access.upper()
        if address < 0 or access not in {"R", "W"}:
            raise ValueError("invalid watchpoint address or access")
        return await self.commander_commands(
            [f"SetWP 0x{address:08X} {access}"],
            selector=selector,
            action="set_watchpoint",
            destructive=True,
        )

    async def clear_watchpoint(
        self, handle: int, *, selector: DeviceSelector | None = None
    ) -> CommandResult:
        if handle < 0 or handle > 255:
            raise ValueError("invalid watchpoint handle")
        return await self.commander_commands(
            [f"ClearWP {handle}"],
            selector=selector,
            action="clear_watchpoint",
            destructive=True,
        )

    async def erase_flash(
        self,
        start: int | None = None,
        end: int | None = None,
        *,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        if (start is None) != (end is None):
            raise ValueError("start and end must be provided together")
        if start is not None and (start < 0 or end is None or end <= start):
            raise ValueError("invalid erase range")
        command = "Erase" if start is None else f"Erase 0x{start:X}, 0x{end:X}"
        return await self.commander_commands(
            [command],
            selector=selector,
            action="erase_flash",
            destructive=True,
            timeout=180,
        )

    async def verify_binary(
        self,
        path: str,
        address: int,
        *,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        artifact = self.settings.resolve_allowed_path(path)
        if artifact.suffix.lower() != ".bin" or address < 0:
            raise ValueError("verification requires a BIN and destination address")
        return await self.commander_commands(
            [f'VerifyBin "{artifact}", 0x{address:X}'],
            selector=selector,
            action="verify_binary",
            destructive=False,
            timeout=180,
        )

    async def probe_info(self, selector: DeviceSelector | None = None) -> CommandResult:
        return await self.commander_commands(
            ["ShowFWInfo", "ShowHWStatus", "ShowConf", "Uptime"],
            selector=selector,
            action="probe_info",
            destructive=False,
        )

    async def command_string(
        self,
        command: str,
        *,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        from .security import validate_raw_command

        validated = validate_raw_command(command)
        return await self.commander_commands(
            [f"Exec {validated}"],
            selector=selector,
            action="jlink_command_string",
            destructive=True,
        )

    async def swo(
        self,
        action: str,
        *,
        speed_hz: int | None = None,
        capture_ms: int = 500,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        action = action.lower()
        if action == "speeds":
            commands = ["SWOSpeed"]
            destructive = False
        elif action == "status":
            commands = ["SWOStat"]
            destructive = False
        elif action == "stop":
            commands = ["SWOStop"]
            destructive = True
        elif action == "capture":
            if not 1 <= capture_ms <= 300_000:
                raise ValueError("capture_ms must be between 1 and 300000")
            if speed_hz is not None and not 1 <= speed_hz <= 4_000_000:
                raise ValueError("EDU Mini SWO speed cannot exceed 4 MHz")
            start = "SWOStart" if speed_hz is None else f"SWOStart {speed_hz}"
            commands = [start, f"Sleep {capture_ms}", "SWORead", "SWOStop"]
            destructive = True
        else:
            raise ValueError("action must be speeds, status, stop, or capture")
        return await self.commander_commands(
            commands,
            selector=selector,
            action=f"swo_{action}",
            destructive=destructive,
            timeout=max(30, capture_ms / 1000 + 10),
        )

    async def clear_breakpoint(
        self, handle: int, *, selector: DeviceSelector | None = None
    ) -> CommandResult:
        if handle < 0 or handle > 255:
            raise ValueError("invalid breakpoint handle")
        return await self.commander_commands(
            [f"ClearBP {handle}"],
            selector=selector,
            action="clear_breakpoint",
            destructive=True,
        )

    async def raw(
        self,
        commands: list[str],
        *,
        selector: DeviceSelector | None = None,
        destructive: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        derived_destructive = any(
            command.split(maxsplit=1)[0].lower() not in _READ_ONLY_COMMANDER_COMMANDS
            for command in commands
            if command.strip()
        )
        return await self.commander_commands(
            commands,
            selector=selector,
            action="raw_commander",
            destructive=destructive or derived_destructive,
            timeout=timeout,
        )

    async def run_application(
        self,
        application: str,
        args: list[str],
        *,
        timeout: float | None = None,
        destructive: bool = True,
        selector: DeviceSelector | None = None,
        resume_after_preflight: bool = False,
        resume_settle_seconds: float = 0.0,
        attempts: int = 1,
        retry_delay_seconds: float = 0.0,
    ) -> CommandResult:
        if isinstance(attempts, bool) or not 1 <= attempts <= 3:
            raise ValueError("attempts must be between 1 and 3")
        if not 0 <= retry_delay_seconds <= 5:
            raise ValueError("retry_delay_seconds must be between 0 and 5")
        if retry_delay_seconds and attempts == 1:
            raise ValueError("retry_delay_seconds requires more than one attempt")
        if not 0 <= resume_settle_seconds <= 5:
            raise ValueError("resume_settle_seconds must be between 0 and 5")
        if resume_settle_seconds and not resume_after_preflight:
            raise ValueError("resume_settle_seconds requires resume_after_preflight")
        if not destructive and (
            len(args) != 1
            or args[0].lower() not in _INFORMATIONAL_APPLICATION_ARGUMENTS
        ):
            raise ValueError(
                "non-destructive SEGGER application use is limited to one help/version argument"
            )
        resolved: DeviceSelector | None = None
        identity: CommandResult | None = None
        if destructive:
            resolved = await self.resolve_selector_wait(selector)
            assert resolved.probe_serial is not None
            async with self.leases.lease(
                resolved.probe_serial,
                owner=f"application:{application}",
                timeout=timeout or self.settings.default_timeout_seconds,
            ) as lease:
                identity = await self._identity_preflight(resolved, lease.lease_id)
                if resume_after_preflight:
                    await self._resume_after_identity_preflight(
                        resolved,
                        lease.lease_id,
                        identity.parsed,
                        action=f"application_resume:{application}",
                    )
                    if resume_settle_seconds:
                        # A connect-under-reset preflight can restart firmware
                        # whose application-owned RAM state (for example RTT)
                        # is initialized shortly after Go. Keep the probe lease
                        # while allowing that state to become observable.
                        await asyncio.sleep(resume_settle_seconds)
                attempt_evidence: list[dict[str, Any]] = []
                for attempt in range(1, attempts + 1):
                    result = await self.application.execute(
                        application, args, timeout=timeout
                    )
                    attempt_evidence.append(
                        {
                            "attempt": attempt,
                            "started_at": result.started_at.isoformat(),
                            "finished_at": result.finished_at.isoformat(),
                            "duration_ms": result.duration_ms,
                            "return_code": result.return_code,
                            "timed_out": result.timed_out,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                        }
                    )
                    if result.ok or result.timed_out or attempt == attempts:
                        break
                    if retry_delay_seconds:
                        await asyncio.sleep(retry_delay_seconds)
                result.parsed.setdefault("application_attempts", attempt_evidence)
        else:
            result = await self.application.execute(application, args, timeout=timeout)
        if resolved and identity:
            result.session_id = identity.session_id
            self._attach_identities(result, resolved, identity.parsed)
        self.store.append_operation(
            result=result,
            action=f"application:{application}",
            probe_serial=resolved.probe_serial if resolved else None,
            destructive=destructive,
            request={
                "application": application,
                "args": args,
                "resume_after_preflight": resume_after_preflight,
                "resume_settle_seconds": resume_settle_seconds,
                "attempts": attempts,
                "retry_delay_seconds": retry_delay_seconds,
                "selector": resolved.model_dump(mode="json") if resolved else None,
            },
        )
        return result

    @staticmethod
    def _serial_port_ready(path: str) -> bool:
        """Require the transient CDC node, not only its sysfs metadata."""

        return Path(path).exists()

    async def _wait_for_serial_board(
        self,
        resolved: DeviceSelector,
        *,
        timeout: float = 10.0,
    ) -> Any:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            board = next(
                (
                    item
                    for item in self.capabilities().boards
                    if item.serial == resolved.board_serial
                    and item.serial_port
                    and self._serial_port_ready(item.serial_port)
                ),
                None,
            )
            if board is not None:
                return board
            await asyncio.sleep(0.2)
        raise RuntimeError(
            "selected board did not expose an accessible serial port within "
            f"{timeout:g} seconds"
        )

    async def _resume_after_identity_preflight(
        self,
        resolved: DeviceSelector,
        lease_id: str,
        identity_data: dict[str, Any],
        *,
        action: str,
    ) -> CommandResult:
        """Resume execution after Commander's identity attach, with evidence."""

        result = await self.commander.execute(
            ["Go"],
            selector=resolved,
            timeout=self.settings.default_timeout_seconds,
        )
        result.session_id = lease_id
        result.parsed.setdefault("selector", resolved.model_dump(mode="json"))
        result.parsed.setdefault("identity_preflight", identity_data)
        self._attach_identities(result, resolved, identity_data)
        self.store.append_operation(
            result=result,
            action=action,
            probe_serial=resolved.probe_serial,
            destructive=True,
            request={
                "selector": resolved.model_dump(mode="json"),
                "commands": ["Go"],
            },
        )
        if not result.ok:
            raise RuntimeError(
                "target could not be resumed after identity preflight: "
                f"{result.stderr or result.stdout}"
            )
        return result

    async def serial_exchange(
        self,
        *,
        selector: DeviceSelector | None = None,
        write: str | None = None,
        baudrate: int = 115200,
        duration: float = 2.0,
        until: str | None = None,
    ) -> CommandResult:
        resolved = await self.resolve_selector_wait(selector)
        matching_gdb = next(
            (
                session_id
                for session_id, active in self._gdb_selectors.items()
                if active.probe_serial == resolved.probe_serial
                and active.board_serial == resolved.board_serial
                and active.core == resolved.core
            ),
            None,
        )
        identity_data: dict[str, Any]
        resume_operation_id: str | None = None
        if matching_gdb:
            identity_data = self._gdb_identities[matching_gdb]
            board = await self._wait_for_serial_board(resolved)
            result = await self.serial.exchange(
                board.serial_port,
                write=write,
                baudrate=baudrate,
                duration=duration,
                until=until,
            )
            result.session_id = matching_gdb
        else:
            assert resolved.probe_serial is not None
            async with self.leases.lease(
                resolved.probe_serial,
                owner="serial_exchange",
                timeout=self.settings.default_timeout_seconds,
            ) as lease:
                identity = await self._identity_preflight(resolved, lease.lease_id)
                identity_data = identity.parsed
                resume = await self._resume_after_identity_preflight(
                    resolved,
                    lease.lease_id,
                    identity_data,
                    action="serial_resume",
                )
                resume_operation_id = resume.operation_id
                # Commander attach can briefly re-enumerate the target's USB
                # CDC interface.  Resolve it by the stable board serial only
                # after positive target identification has completed.
                board = await self._wait_for_serial_board(resolved)
                result = await self.serial.exchange(
                    board.serial_port,
                    write=write,
                    baudrate=baudrate,
                    duration=duration,
                    until=until,
                )
                result.session_id = lease.lease_id
        result.parsed["selector"] = resolved.model_dump(mode="json")
        if resume_operation_id:
            result.parsed["resume_operation_id"] = resume_operation_id
        self._attach_identities(result, resolved, identity_data)
        self.store.append_operation(
            result=result,
            action="serial_exchange",
            probe_serial=resolved.probe_serial,
            destructive=write is not None,
            request={"write": write, "duration": duration, "until": until},
        )
        return result

    async def audited_serial_operation(
        self,
        operation: Callable[[str], Awaitable[CommandResult]],
        *,
        selector: DeviceSelector | None,
        action: str,
        destructive: bool,
        request: dict[str, Any],
        timeout: float,
    ) -> CommandResult:
        """Run one extension serial operation behind identity, lease, and audit gates."""

        resolved = await self.resolve_selector_wait(selector)
        matching_gdb = next(
            (
                session_id
                for session_id, active in self._gdb_selectors.items()
                if active.probe_serial == resolved.probe_serial
                and active.board_serial == resolved.board_serial
                and active.core == resolved.core
            ),
            None,
        )
        identity_data: dict[str, Any]
        resume_operation_id: str | None = None
        if matching_gdb:
            identity_data = self._gdb_identities[matching_gdb]
            board = await self._wait_for_serial_board(resolved)
            assert board.serial_port is not None
            result = await operation(board.serial_port)
            result.session_id = matching_gdb
        else:
            assert resolved.probe_serial is not None
            async with self.leases.lease(
                resolved.probe_serial,
                owner=action,
                timeout=max(timeout, self.settings.default_timeout_seconds),
            ) as lease:
                identity = await self._identity_preflight(resolved, lease.lease_id)
                identity_data = identity.parsed
                resume = await self._resume_after_identity_preflight(
                    resolved,
                    lease.lease_id,
                    identity_data,
                    action=f"{action}_resume",
                )
                resume_operation_id = resume.operation_id
                board = await self._wait_for_serial_board(resolved)
                assert board.serial_port is not None
                result = await operation(board.serial_port)
                result.session_id = lease.lease_id
        result.parsed["selector"] = resolved.model_dump(mode="json")
        if resume_operation_id:
            result.parsed["resume_operation_id"] = resume_operation_id
        self._attach_identities(result, resolved, identity_data)
        self.store.append_operation(
            result=result,
            action=action,
            probe_serial=resolved.probe_serial,
            destructive=destructive,
            request={
                "selector": resolved.model_dump(mode="json"),
                **request,
            },
        )
        return result

    async def start_gdb(
        self,
        *,
        selector: DeviceSelector | None = None,
        elf_path: str | None = None,
    ) -> dict[str, Any]:
        resolved = await self.resolve_selector_wait(selector)
        assert resolved.probe_serial is not None
        path = self.settings.resolve_allowed_path(elf_path) if elf_path else None
        lease = await self.leases.acquire(
            resolved.probe_serial,
            owner="gdb_session",
            timeout=self.settings.default_timeout_seconds,
        )
        try:
            identity = await self._identity_preflight(resolved, lease.lease_id)
            session_id = await self.gdb.start(resolved, elf_path=path)
        except BaseException:
            await self.leases.release(lease.lease_id)
            raise
        self._gdb_leases[session_id] = lease.lease_id
        self._gdb_selectors[session_id] = resolved
        self._gdb_identities[session_id] = dict(identity.parsed)
        info = self.gdb.session_info(session_id)
        self.store.upsert_session(
            session_id=session_id,
            probe_serial=resolved.probe_serial,
            backend=self.gdb.name,
            state=info,
        )
        now = datetime.now(UTC)
        audit = CommandResult(
            operation_id=str(uuid.uuid4()),
            session_id=session_id,
            backend=self.gdb.name,
            command=["start-session", str(path) if path else ""],
            started_at=now,
            finished_at=now,
            duration_ms=0,
            return_code=0,
            parsed=info,
            probe_identity={"serial": resolved.probe_serial},
            target_identity={
                "board_serial": resolved.board_serial,
                "target_profile": resolved.target_profile,
                "core": resolved.core,
            },
        )
        self.store.append_operation(
            result=audit,
            action="start_gdb_session",
            probe_serial=resolved.probe_serial,
            destructive=True,
            request={"elf_path": str(path) if path else None},
        )
        return info

    async def start_gui(
        self,
        application: str,
        args: list[str],
        *,
        selector: DeviceSelector | None = None,
    ) -> dict[str, Any]:
        resolved = await self.resolve_selector_wait(selector)
        assert resolved.probe_serial is not None
        lease = await self.leases.acquire(
            resolved.probe_serial,
            owner=f"gui:{application}",
            timeout=self.settings.default_timeout_seconds,
        )
        try:
            identity = await self._identity_preflight(resolved, lease.lease_id)
            session_id = await self.gui.launch(application, args)
        except BaseException:
            await self.leases.release(lease.lease_id)
            raise
        self._gui_leases[session_id] = lease.lease_id
        self._gui_selectors[session_id] = resolved
        now = datetime.now(UTC)
        result = CommandResult(
            operation_id=str(uuid.uuid4()),
            session_id=session_id,
            backend=self.gui.name,
            command=[application, *args],
            started_at=now,
            finished_at=now,
            duration_ms=0,
            return_code=0,
        )
        self._attach_identities(result, resolved, identity.parsed)
        self.store.append_operation(
            result=result,
            action="launch_gui",
            probe_serial=resolved.probe_serial,
            destructive=True,
            request={"application": application, "args": args},
        )
        return {
            "session_id": session_id,
            "operation_id": result.operation_id,
            "application": application,
            "selector": resolved.model_dump(mode="json"),
        }

    def _audit_gui_result(
        self, session_id: str, result: CommandResult, action: str, *, destructive: bool
    ) -> CommandResult:
        selector = self._gui_selectors.get(session_id)
        result.session_id = session_id
        if selector:
            result.probe_identity = {"serial": selector.probe_serial}
            result.target_identity = {
                "board_serial": selector.board_serial,
                "target_profile": selector.target_profile,
                "core": selector.core,
            }
        self.store.append_operation(
            result=result,
            action=action,
            probe_serial=selector.probe_serial if selector else None,
            destructive=destructive,
            request={"session_id": session_id},
        )
        return result

    async def gui_keys(self, session_id: str, keys: str) -> CommandResult:
        return self._audit_gui_result(
            session_id,
            await self.gui.keys(session_id, keys),
            "gui_keys",
            destructive=True,
        )

    async def gui_click(self, session_id: str, x: int, y: int) -> CommandResult:
        return self._audit_gui_result(
            session_id,
            await self.gui.click(session_id, x, y),
            "gui_click",
            destructive=True,
        )

    async def gui_screenshot(self, session_id: str) -> CommandResult:
        return self._audit_gui_result(
            session_id,
            await self.gui.screenshot(session_id),
            "gui_screenshot",
            destructive=False,
        )

    async def gui_ocr(self, screenshot_path: str) -> CommandResult:
        """OCR a confined screenshot and record the input hash in the audit chain."""

        path = self.settings.resolve_allowed_path(screenshot_path)
        result = await self.gui.ocr(path)
        result.artifact_hashes[str(path)] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        self.store.append_operation(
            result=result,
            action="gui_ocr",
            probe_serial=None,
            destructive=False,
            request={"screenshot_path": str(path)},
        )
        return result

    async def gui_accessibility(self, session_id: str) -> CommandResult:
        return self._audit_gui_result(
            session_id,
            await self.gui.accessibility_tree(session_id),
            "gui_accessibility",
            destructive=False,
        )

    def gui_session_info(self, session_id: str) -> dict[str, Any]:
        info = self.gui.session_info(session_id)
        selector = self._gui_selectors.get(session_id)
        now = datetime.now(UTC)
        result = CommandResult(
            operation_id=str(uuid.uuid4()),
            session_id=session_id,
            backend=self.gui.name,
            command=["session-info"],
            started_at=now,
            finished_at=now,
            duration_ms=0,
            return_code=0,
            parsed=info,
        )
        self._audit_gui_result(
            session_id, result, "gui_session_info", destructive=False
        )
        return {
            **info,
            "operation_id": result.operation_id,
            "selector": selector.model_dump(mode="json") if selector else None,
        }

    async def gui_image_match(
        self, session_id: str, template: Path, *, threshold: float
    ) -> CommandResult:
        return self._audit_gui_result(
            session_id,
            await self.gui.image_match(session_id, template, threshold=threshold),
            "gui_image_match",
            destructive=False,
        )

    async def stop_gui(self, session_id: str) -> None:
        selector = self._gui_selectors.pop(session_id, None)
        try:
            await self.gui.stop(session_id)
        finally:
            lease_id = self._gui_leases.pop(session_id, None)
            if lease_id:
                await self.leases.release(lease_id)
        if selector:
            now = datetime.now(UTC)
            result = CommandResult(
                operation_id=str(uuid.uuid4()),
                session_id=session_id,
                backend=self.gui.name,
                command=["stop-session"],
                started_at=now,
                finished_at=now,
                duration_ms=0,
                return_code=0,
                probe_identity={"serial": selector.probe_serial},
                target_identity={
                    "board_serial": selector.board_serial,
                    "target_profile": selector.target_profile,
                    "core": selector.core,
                },
            )
            self.store.append_operation(
                result=result,
                action="stop_gui",
                probe_serial=selector.probe_serial,
                destructive=False,
                request={"session_id": session_id},
            )

    async def gdb_command(
        self, session_id: str, command: str, *, timeout: float = 30
    ) -> CommandResult:
        result = await self.gdb.command(session_id, command, timeout=timeout)
        info = self.gdb.session_info(session_id)
        self.store.append_operation(
            result=result,
            action="gdb_command",
            probe_serial=str(info["probe_serial"]),
            destructive=True,
            request={"session_id": session_id, "command": command},
        )
        return result

    async def stop_gdb(self, session_id: str, *, resume: bool = True) -> None:
        info: dict[str, Any] | None = None
        try:
            try:
                info = self.gdb.session_info(session_id)
            except ValueError:
                pass
            await self.gdb.stop(session_id, resume=resume)
            self.store.delete_session(session_id)
        finally:
            lease_id = self._gdb_leases.pop(session_id, None)
            self._gdb_selectors.pop(session_id, None)
            self._gdb_identities.pop(session_id, None)
            if lease_id:
                await self.leases.release(lease_id)
        if info:
            now = datetime.now(UTC)
            audit = CommandResult(
                operation_id=str(uuid.uuid4()),
                session_id=session_id,
                backend=self.gdb.name,
                command=["stop-session", f"resume={resume}"],
                started_at=now,
                finished_at=now,
                duration_ms=0,
                return_code=0,
                parsed={"session_id": session_id, "resume": resume},
                probe_identity={"serial": info.get("probe_serial")},
            )
            self.store.append_operation(
                result=audit,
                action="stop_gdb_session",
                probe_serial=str(info.get("probe_serial")),
                destructive=resume,
                request={"session_id": session_id, "resume": resume},
            )

    async def capture_gdb_channel(
        self,
        session_id: str,
        channel: str,
        *,
        duration: float = 2.0,
        write: str | None = None,
    ) -> CommandResult:
        result = await self.gdb.capture_port(
            session_id, channel, duration=duration, write=write
        )
        info = self.gdb.session_info(session_id)
        self.store.append_operation(
            result=result,
            action=f"capture_{channel}",
            probe_serial=str(info["probe_serial"]),
            destructive=write is not None,
            request={"session_id": session_id, "duration": duration},
        )
        return result

    def inspect_elf(self, path: str) -> dict[str, Any]:
        resolved = self.settings.resolve_allowed_path(path)
        return inspect_elf(resolved)

    async def close(self) -> None:
        for session_id in list(self._gdb_leases):
            await self.stop_gdb(session_id)
        for session_id in list(self._gui_leases):
            await self.stop_gui(session_id)
        await self.gui.stop_all()


def _effective_capabilities() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("CapEff:"):
                return int(line.split(":", 1)[1].strip(), 16)
    except OSError:
        pass
    return -1
