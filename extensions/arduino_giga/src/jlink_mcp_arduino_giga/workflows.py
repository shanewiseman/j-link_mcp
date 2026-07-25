"""Build, flash, backup, and validation workflows."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import (
    finalize_fixture_elf,
    verify_fixture_elf,
)
from jlink_mcp.artifacts import inspect_elf, registerable_artifact
from jlink_mcp.models import (
    Artifact,
    CommandResult,
    ValidationStep,
)
from jlink_mcp.service import JLinkService
from jlink_mcp.workflows import Workflows

from .config import ArduinoGigaConfig
from .models import BuildResult, DeviceSelector, ValidationReport
from .profiles import get_profile
from .profiles import TargetCore

_PROPERTY_RE = re.compile(r"^([^=\s]+)=(.*)$")
class ArduinoGigaWorkflows(Workflows):
    def __init__(self, service: JLinkService, config: ArduinoGigaConfig) -> None:
        super().__init__(service)
        self.config = config

    async def build_firmware(
        self,
        sketch: str,
        *,
        core: TargetCore,
        flash_split: str | None = None,
        clean: bool = True,
    ) -> BuildResult:
        sketch_path = self._resolve_sketch_path(sketch)
        split = flash_split or self.config.flash_split
        if split not in {"100_0", "75_25", "50_50"}:
            raise ValueError("flash_split must be 100_0, 75_25, or 50_50")
        profile = get_profile("arduino_giga_r1")
        build_dir = (
            self.settings.state_root
            / "artifacts"
            / "builds"
            / f"{sketch_path.stem}-{core.value}-{uuid.uuid4()}"
        )
        build_dir.mkdir(parents=True)
        options = f"target_core={core.value.replace('m', 'cm')},split={split}"
        identity = await self._build_identity()
        staged_sketch = build_dir / "source" / sketch_path.name
        shutil.copytree(sketch_path, staged_sketch)
        (staged_sketch / "JLinkMCPBuildIdentity.h").write_text(
            "#pragma once\n"
            f'#define JLINK_MCP_GIT_COMMIT "{identity["git_commit"]}"\n'
            f'#define JLINK_MCP_BUILD_ID "{identity["build_id"]}"\n'
            f'#define JLINK_MCP_BUILD_TIMESTAMP "{identity["build_timestamp"]}"\n',
            encoding="utf-8",
        )
        argv = [
            self.config.arduino_cli,
            "compile",
            "--fqbn",
            str(profile.metadata["fqbn"]),
            "--board-options",
            options,
            "--output-dir",
            str(build_dir),
            "--export-binaries",
        ]
        if clean:
            argv.append("--clean")
        argv.append(str(staged_sketch))
        command = await self.service.runner.run(
            argv,
            backend="arduino-cli",
            cwd=self.settings.workspace_root,
            timeout=900,
        )
        self.service.store.append_operation(
            result=command,
            action="build_firmware",
            probe_serial=None,
            destructive=False,
            request={
                "sketch": str(sketch_path),
                "staged_sketch": str(staged_sketch),
                "core": core.value,
                "split": split,
            },
        )
        artifacts: list[Artifact] = []
        embedded_manifest: dict[str, Any] = {}
        properties = await self._build_properties(
            sketch_path, core=core, flash_split=split
        )
        if command.ok:
            elf_path = next(build_dir.glob("*.elf"), None)
            if elf_path is None:
                raise RuntimeError("Arduino build succeeded without an ELF artifact")
            embedded_manifest = finalize_fixture_elf(elf_path)
            embedded_manifest["verification"] = verify_fixture_elf(elf_path)
            await self._regenerate_flash_artifacts(
                elf_path, build_dir=build_dir, properties=properties
            )
            await self._generate_analysis_artifacts(
                elf_path, build_dir=build_dir, properties=properties
            )
            for path in sorted(build_dir.iterdir()):
                if ".with_bootloader." in path.name:
                    # Arduino emits these before post-link finalization; never
                    # register them as normal validation images.
                    continue
                if path.is_file() and path.suffix.lower() in {
                    ".elf",
                    ".bin",
                    ".hex",
                    ".map",
                    ".symbols",
                    ".disassembly",
                    ".json",
                }:
                    artifact = registerable_artifact(
                        path, kind=path.suffix.lower().lstrip(".")
                    )
                    if path.suffix.lower() == ".elf":
                        artifact.metadata["elf"] = inspect_elf(path)
                    self.service.store.register_artifact(artifact)
                    artifacts.append(artifact)
        manifest_path = build_dir / "jlink-mcp-build-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "core": core.value,
                    "fqbn": profile.metadata["fqbn"],
                    "flash_split": split,
                    "identity": identity,
                    "embedded_manifest": embedded_manifest,
                    "properties": properties,
                    "artifacts": [
                        artifact.model_dump(mode="json") for artifact in artifacts
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_artifact = registerable_artifact(manifest_path, kind="manifest")
        self.service.store.register_artifact(manifest_artifact)
        artifacts.append(manifest_artifact)
        checksum_path = build_dir / "jlink-mcp-checksums.json"
        checksum_path.write_text(
            json.dumps(
                {artifact.path: artifact.sha256 for artifact in artifacts},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        checksum_artifact = registerable_artifact(checksum_path, kind="checksums")
        self.service.store.register_artifact(checksum_artifact)
        artifacts.append(checksum_artifact)
        return BuildResult(
            core=core,
            fqbn=str(profile.metadata["fqbn"]),
            build_directory=str(build_dir),
            command=command,
            artifacts=artifacts,
            properties=properties,
        )

    def _resolve_sketch_path(self, sketch: str) -> Path:
        legacy_root = Path("firmware/giga_hil")
        requested = Path(sketch)
        try:
            relative = requested.relative_to(legacy_root)
        except ValueError:
            return self.settings.resolve_workspace_path(sketch)
        packaged = Path(__file__).parent / "firmware" / "giga_hil" / relative
        return packaged.resolve(strict=True)

    async def _build_identity(self) -> dict[str, str]:
        timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
        revision = await self.service.runner.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            backend="git",
            cwd=self.settings.repository_root,
            timeout=10,
        )
        commit = revision.stdout.strip() if revision.ok else ""
        identity_kind = "git-commit"
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            commit = self._source_tree_identity()
            identity_kind = "source-tree-sha1"
        return {
            "git_commit": commit,
            "identity_kind": identity_kind,
            "build_id": f"{timestamp.replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}",
            "build_timestamp": timestamp,
        }

    def _source_tree_identity(self) -> str:
        """Hash publishable inputs when HEAD does not exist (for fresh handoffs)."""

        digest = hashlib.sha1(usedforsecurity=False)
        excluded = {".git", ".venv", "state", "__pycache__", ".pytest_cache"}
        for path in sorted(self.settings.repository_root.rglob("*")):
            if not path.is_file() or any(part in excluded for part in path.parts):
                continue
            if path.name == ".token" or path.name == ".env" or path.name.startswith(".env."):
                continue
            relative = path.relative_to(self.settings.repository_root).as_posix()
            digest.update(relative.encode("utf-8") + b"\0")
            with path.open("rb") as handle:
                while block := handle.read(1024 * 1024):
                    digest.update(block)
            digest.update(b"\0")
        return digest.hexdigest()

    async def _regenerate_flash_artifacts(
        self,
        elf_path: Path,
        *,
        build_dir: Path,
        properties: dict[str, str],
    ) -> None:
        compiler_path = Path(properties["build.compiler_path"])
        objcopy = compiler_path / "arm-none-eabi-objcopy"
        for output_format, suffix, extra in (
            ("binary", ".bin", []),
            ("ihex", ".hex", ["-R", ".eeprom"]),
        ):
            destination = build_dir / f"{elf_path.stem}{suffix}"
            result = await self.service.runner.run(
                [objcopy, "-O", output_format, *extra, elf_path, destination],
                backend="arm-none-eabi-objcopy",
                cwd=self.settings.workspace_root,
                timeout=120,
            )
            self.service.store.append_operation(
                result=result,
                action=f"generate_{output_format}",
                probe_serial=None,
                destructive=False,
            )
            if not result.ok:
                raise RuntimeError(f"objcopy failed: {result.stderr or result.stdout}")

    async def _generate_analysis_artifacts(
        self,
        elf_path: Path,
        *,
        build_dir: Path,
        properties: dict[str, str],
    ) -> None:
        compiler_path = Path(properties["build.compiler_path"])
        jobs = (
            (
                compiler_path / "arm-none-eabi-nm",
                ["-n", "-S", "--defined-only", elf_path],
                build_dir / f"{elf_path.stem}.symbols",
            ),
            (
                compiler_path / "arm-none-eabi-objdump",
                ["-d", "-S", "-l", elf_path],
                build_dir / f"{elf_path.stem}.disassembly",
            ),
        )
        for executable, args, destination in jobs:
            result = await self.service.runner.run(
                [executable, *args],
                backend=executable.name,
                cwd=self.settings.workspace_root,
                timeout=180,
            )
            self.service.store.append_operation(
                result=result,
                action=f"generate_{destination.suffix.lstrip('.')}",
                probe_serial=None,
                destructive=False,
            )
            if not result.ok:
                raise RuntimeError(
                    f"{executable.name} failed: {result.stderr or result.stdout}"
                )
            destination.write_text(result.stdout, encoding="utf-8")

    async def _build_properties(
        self, sketch_path: Path, *, core: TargetCore, flash_split: str
    ) -> dict[str, str]:
        options = f"target_core={core.value.replace('m', 'cm')},split={flash_split}"
        result = await self.service.runner.run(
            [
                self.config.arduino_cli,
                "compile",
                "--fqbn",
                self.config.fqbn,
                "--board-options",
                options,
                "--show-properties",
                str(sketch_path),
            ],
            backend="arduino-cli-properties",
            cwd=self.settings.workspace_root,
            timeout=120,
        )
        properties: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if match := _PROPERTY_RE.match(line):
                properties[match.group(1)] = match.group(2)
        return properties

    async def flash_and_verify(
        self,
        artifact_path: str,
        *,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        path = self.settings.resolve_allowed_path(artifact_path)
        if path.suffix.lower() not in {".elf", ".hex", ".bin"}:
            raise ValueError("flash artifact must be ELF, HEX, or BIN")
        commands = ["Reset", f'LoadFile "{path}"']
        if path.suffix.lower() == ".bin":
            raise ValueError(
                "raw BIN flashing requires an explicit destination address; "
                "use flash_binary"
            )
        # Commander 9.62 has VerifyBin, but no VerifyFile command. LoadFile
        # programs and verifies address-bearing ELF/HEX/S-record inputs and
        # exits non-zero under -ExitOnError when verification fails.
        commands.extend(["Reset", "Go"])
        result = await self.service.commander_commands(
            commands,
            selector=selector,
            action="flash_and_verify",
            destructive=True,
            timeout=180,
        )
        artifact = registerable_artifact(path, kind=path.suffix.lower().lstrip("."))
        self.service.store.register_artifact(artifact)
        result.artifact_hashes[str(path)] = artifact.sha256
        return result

    async def flash_binary(
        self,
        artifact_path: str,
        address: int,
        *,
        selector: DeviceSelector | None = None,
    ) -> CommandResult:
        path = self.settings.resolve_allowed_path(artifact_path)
        if path.suffix.lower() != ".bin" or address < 0:
            raise ValueError("flash_binary requires a BIN file and valid address")
        result = await self.service.commander_commands(
            [
                "Reset",
                f'LoadBin "{path}", 0x{address:08X}',
                f'VerifyBin "{path}", 0x{address:08X}',
                "Reset",
                "Go",
            ],
            selector=selector,
            action="flash_binary",
            destructive=True,
            timeout=180,
        )
        artifact = registerable_artifact(path, kind="bin")
        self.service.store.register_artifact(artifact)
        result.artifact_hashes[str(path)] = artifact.sha256
        return result

    async def backup_flash(
        self,
        address: int,
        size: int,
        *,
        selector: DeviceSelector | None = None,
    ) -> tuple[CommandResult, Artifact | None]:
        if address < 0 or size <= 0 or size > 32 * 1024 * 1024:
            raise ValueError("invalid backup range")
        destination = (
            self.settings.state_root
            / "artifacts"
            / f"flash-backup-{address:08x}-{size:x}-{uuid.uuid4()}.bin"
        )
        result = await self.service.commander_commands(
            [f'SaveBin "{destination}", 0x{address:08X}, 0x{size:X}'],
            selector=selector,
            action="backup_flash",
            destructive=False,
            timeout=max(60, size / 200_000 * 2),
        )
        artifact = None
        if result.ok and destination.exists():
            artifact = registerable_artifact(destination, kind="flash-backup")
            self.service.store.register_artifact(artifact)
            result.artifact_hashes[str(destination)] = artifact.sha256
        return result, artifact

    async def hardware_preflight(
        self,
        *,
        selector: DeviceSelector | None = None,
        prepare_dual_core: bool = False,
    ) -> dict[str, Any]:
        """Identify both cores and snapshot non-destructive STM32H747 state."""

        resolved = await self.service.resolve_selector_wait(selector)
        m7 = resolved.model_copy(update={"core": TargetCore.M7})
        m4 = resolved.model_copy(update={"core": TargetCore.M4})
        preparation = None
        if prepare_dual_core:
            preparation = await self.prepare_giga_dual_core_debug(selector=resolved)
            m7_identity = preparation["m7_identity"]
            m4_identity = preparation["m4_identity"]
            identities_ok = bool(preparation["ok"])
        else:
            m7_result = await self.service.connect(m7)
            m4_result = await self.service.connect(m4)
            m7_identity = m7_result.model_dump(mode="json")
            m4_identity = m4_result.model_dump(mode="json")
            identities_ok = m7_result.ok and m4_result.ok
        doctor = self.service.doctor()
        registers = await self.service.raw(
            [
                "Mem32 0x5C001000 1",  # DBGMCU_IDCODE
                "Mem32 0x5200201C 1",  # FLASH1_OPTSR_CUR
                "Mem32 0x52002020 1",  # FLASH1_OPTSR_PRG
                "Mem32 0x5200211C 1",  # FLASH2_OPTSR_CUR
                "Mem32 0x52002120 1",  # FLASH2_OPTSR_PRG
                "Mem32 0x52002038 1",  # FLASH1_WPSN_CUR
                "Mem32 0x5200203C 1",  # FLASH1_WPSN_PRG
                "Mem32 0x52002138 1",  # FLASH2_WPSN_CUR
                "Mem32 0x5200213C 1",  # FLASH2_WPSN_PRG
                "Mem32 0x52002040 1",  # BOOT_CURR
                "Mem32 0x52002044 1",  # BOOT_PRGR
            ],
            selector=m7,
            destructive=False,
        )
        return {
            "ok": doctor.ok and identities_ok and registers.ok,
            "selector": resolved.model_dump(mode="json"),
            "doctor": doctor.model_dump(mode="json"),
            "prepare_dual_core": prepare_dual_core,
            "preparation": preparation,
            "m7_identity": m7_identity,
            "m4_identity": m4_identity,
            "register_snapshot": registers.model_dump(mode="json"),
        }

    async def prepare_giga_dual_core_debug(
        self, *, selector: DeviceSelector | None = None
    ) -> dict[str, Any]:
        """Transiently release an option-held CM4 after positive M7 identity."""

        resolved = await self.service.resolve_selector_wait(selector)
        m7 = resolved.model_copy(update={"core": TargetCore.M7})
        m4 = resolved.model_copy(update={"core": TargetCore.M4})
        m7_identity = await self.service.connect(m7)
        rcc_gcr_address = 0x580244A0
        snapshot = await self.service.read_memory(
            rcc_gcr_address, count=1, width=32, selector=m7
        )
        try:
            gcr_before = int(snapshot.parsed["memory"][0]["values"][0], 0)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("could not read STM32H747 RCC_GCR") from exc
        release = None
        if not gcr_before & 0x8:
            release = await self.service.write_memory(
                rcc_gcr_address, [gcr_before | 0x8], width=32, selector=m7
            )
            if not release.ok:
                raise RuntimeError("could not set STM32H747 RCC_GCR.BOOT_C2")
        await asyncio.sleep(0.5)
        m4_identity = await self.service.connect(m4)
        return {
            "ok": m7_identity.ok
            and snapshot.ok
            and (release is None or release.ok)
            and m4_identity.ok,
            "selector": resolved.model_dump(mode="json"),
            "rcc_gcr_address": f"0x{rcc_gcr_address:08X}",
            "gcr_before": f"0x{gcr_before:08X}",
            "gcr_after": f"0x{gcr_before | 0x8:08X}",
            "changed": release is not None,
            "m7_identity": m7_identity.model_dump(mode="json"),
            "snapshot": snapshot.model_dump(mode="json"),
            "release": release.model_dump(mode="json") if release else None,
            "m4_identity": m4_identity.model_dump(mode="json"),
        }

    @staticmethod
    def _build_artifact(build: BuildResult, kind: str) -> Artifact:
        try:
            return next(artifact for artifact in build.artifacts if artifact.kind == kind)
        except StopIteration as exc:
            raise RuntimeError(f"build produced no {kind} artifact") from exc

    @staticmethod
    def _build_manifest(build: BuildResult) -> dict[str, Any]:
        artifact = ArduinoGigaWorkflows._build_artifact(build, "manifest")
        return json.loads(Path(artifact.path).read_text(encoding="utf-8"))

    async def dual_core_deploy(
        self,
        *,
        selector: DeviceSelector | None = None,
        m7_sketch: str = "firmware/giga_hil/m7",
        m4_sketch: str = "firmware/giga_hil/m4",
        flash_split: str = "75_25",
    ) -> dict[str, Any]:
        """Build both images and program both banks through the M7 access port."""

        resolved = await self.service.resolve_selector_wait(selector)
        access_selector = resolved.model_copy(update={"core": TargetCore.M7})
        m4 = await self.build_firmware(
            m4_sketch, core=TargetCore.M4, flash_split=flash_split
        )
        m7 = await self.build_firmware(
            m7_sketch, core=TargetCore.M7, flash_split=flash_split
        )
        results: dict[str, CommandResult] = {}
        manifests: dict[str, dict[str, Any]] = {}
        identities: dict[str, dict[str, Any]] = {}
        # Program the coprocessor first. Using the M7 access port is required
        # for reliable dual-bank erase/program operations on STM32H747.
        for core, build in (("m4", m4), ("m7", m7)):
            build_manifest = self._build_manifest(build)
            manifest = build_manifest["embedded_manifest"]
            manifests[core] = manifest
            identities[core] = build_manifest["identity"]
            address = int(manifest["flash_start"], 0)
            binary = self._build_artifact(build, "bin")
            results[core] = await self.flash_binary(
                binary.path, address, selector=access_selector
            )
        return {
            "ok": m4.command.ok
            and m7.command.ok
            and all(result.ok for result in results.values()),
            "selector": access_selector.model_dump(mode="json"),
            "m4_build": m4.model_dump(mode="json"),
            "m7_build": m7.model_dump(mode="json"),
            "m4_manifest": manifests["m4"],
            "m7_manifest": manifests["m7"],
            "m4_build_identity": identities["m4"],
            "m7_build_identity": identities["m7"],
            "m4_flash": results["m4"].model_dump(mode="json"),
            "m7_flash": results["m7"].model_dump(mode="json"),
        }

    async def boot_and_observe(
        self,
        *,
        selector: DeviceSelector | None = None,
        m7_elf_path: str | None = None,
        m4_elf_path: str | None = None,
    ) -> dict[str, Any]:
        """Reset both cores and prove serial, heartbeat, manifest, and RPC behavior."""

        resolved = await self.service.resolve_selector_wait(selector)
        m7 = resolved.model_copy(update={"core": TargetCore.M7})
        m4 = resolved.model_copy(update={"core": TargetCore.M4})
        # M7 owns M4 boot through Arduino RPC/OpenAMP. An independent M4 reset
        # races that handshake. A reset-pin system reset gives both cores a
        # cold start, after which M7 boots M4 from the configured split image.
        reset_system = await self.service.reset(
            m7, halt=False, reset_type=2
        )
        observations: dict[str, CommandResult] = {}
        for request in ("PING", "MANIFEST", "INFO", "SELFTEST", "RPC"):
            observations[request.lower()] = await self.service.serial_exchange(
                selector=m7, write=request, duration=3.0
            )
        memory: dict[str, list[CommandResult]] = {}
        for core, elf_path, core_selector in (
            ("m7", m7_elf_path, m7),
            ("m4", m4_elf_path, m4),
        ):
            if not elf_path:
                continue
            symbols = inspect_elf(self.settings.resolve_allowed_path(elf_path))[
                "test_symbols"
            ]
            heartbeat = int(symbols["jlink_mcp_heartbeat"]["address"])
            first = await self.service.read_memory(
                heartbeat, selector=core_selector
            )
            await asyncio.sleep(0.4)
            second = await self.service.read_memory(
                heartbeat, selector=core_selector
            )
            memory[core] = [first, second]
        records = {
            name: result.parsed.get("records", [])
            for name, result in observations.items()
        }

        def heartbeat_progressed(samples: list[CommandResult]) -> bool:
            if len(samples) != 2 or not all(sample.ok for sample in samples):
                return False
            values: list[int] = []
            for sample in samples:
                entries = sample.parsed.get("memory", [])
                if not entries or not entries[0].get("values"):
                    return False
                try:
                    values.append(int(entries[0]["values"][0], 0))
                except (TypeError, ValueError):
                    return False
            return values[1] > values[0]

        heartbeat_progress = {
            core: heartbeat_progressed(samples)
            for core, samples in memory.items()
        }
        return {
            "ok": reset_system.ok
            and all(result.ok for result in observations.values())
            and all(heartbeat_progress.values())
            and any(record.get("event") == "pong" for record in records["ping"])
            and any(record.get("ok") is True for record in records["selftest"])
            and any(
                int(record.get("m4_heartbeat", 0)) > 0
                for record in records["rpc"]
            ),
            "selector": resolved.model_dump(mode="json"),
            "reset_system": reset_system.model_dump(mode="json"),
            "serial": {
                name: result.model_dump(mode="json")
                for name, result in observations.items()
            },
            "heartbeat_memory": {
                core: [item.model_dump(mode="json") for item in samples]
                for core, samples in memory.items()
            },
            "heartbeat_progress": heartbeat_progress,
        }

    async def debug_fixture(
        self,
        elf_path: str,
        *,
        selector: DeviceSelector | None = None,
    ) -> dict[str, Any]:
        """Assert a symbolic breakpoint, watchpoint, stack, memory, and step."""

        resolved = await self.service.resolve_selector_wait(selector)
        info = await self.service.start_gdb(selector=resolved, elf_path=elf_path)
        session_id = str(info["session_id"])
        commands: list[CommandResult] = []

        async def command(text: str, timeout: float = 15) -> CommandResult:
            result = await self.service.gdb_command(
                session_id, text, timeout=timeout
            )
            commands.append(result)
            return result

        try:
            await command("-data-read-memory-bytes &jlink_mcp_test_buffer 32")
            inserted = await command("-break-insert jlink_mcp_breakpoint_site")
            await command("-exec-continue")
            serial_break = await self.service.serial_exchange(
                selector=resolved, write="BREAK", duration=0.5
            )
            await asyncio.sleep(0.3)
            stopped = await command("-thread-info")
            stack = await command("-stack-list-frames")
            locals_result = await command("-stack-list-variables --all-values")
            registers = await command("-data-list-register-values x 13 14 15")
            await command("-break-delete")
            # Complete the BREAK function before arming the watchpoint; its
            # intentional increment must not be mistaken for the SET trigger.
            finished_break = await command("-exec-finish")
            watched = await command("-break-watch jlink_mcp_watch_value")
            await command("-exec-continue")
            serial_watch = await self.service.serial_exchange(
                selector=resolved, write="SET 0xA5A55A5A", duration=0.5
            )
            await asyncio.sleep(0.3)
            watch_stop = await command("-thread-info")
            watch_value = await command(
                "-data-evaluate-expression jlink_mcp_watch_value"
            )
            instruction_step = await command("-exec-next-instruction")
            step_pc = await command("-data-list-register-values x 15")
            source_step = await command("-exec-next")
            ram_write = await command(
                "-data-write-memory-bytes &jlink_mcp_test_buffer "
                "00112233445566778899aabbccddeeff"
            )
            ram_read = await command(
                "-data-read-memory-bytes &jlink_mcp_test_buffer 16"
            )
            await command("-break-delete")
            stopped_messages = [
                item
                for item in stopped.parsed.get("mi", [])
                if item.get("message") == "stopped"
            ]
            watch_messages = [
                item
                for item in watch_stop.parsed.get("mi", [])
                if item.get("message") == "stopped"
            ]
            watch_values = [
                (item.get("payload") or {}).get("value")
                for item in watch_value.parsed.get("mi", [])
                if item.get("message") == "done"
            ]
            memory_contents = [
                memory.get("contents")
                for item in ram_read.parsed.get("mi", [])
                for memory in (item.get("payload") or {}).get("memory", [])
            ]
            return {
                "ok": all(
                    item.ok
                    for item in (
                        inserted,
                        serial_break,
                        stopped,
                        stack,
                        locals_result,
                        registers,
                        finished_break,
                        watched,
                        serial_watch,
                        watch_stop,
                        watch_value,
                        instruction_step,
                        step_pc,
                        source_step,
                        ram_write,
                        ram_read,
                    )
                )
                and any(
                    (item.get("payload") or {}).get("reason") == "breakpoint-hit"
                    for item in stopped_messages
                )
                and any(
                    (item.get("payload") or {}).get("reason") == "watchpoint-trigger"
                    for item in watch_messages
                )
                and "2779077210" in watch_values
                and "00112233445566778899aabbccddeeff" in memory_contents,
                "session": info,
                "commands": [item.model_dump(mode="json") for item in commands],
                "serial_break": serial_break.model_dump(mode="json"),
                "serial_watch": serial_watch.model_dump(mode="json"),
            }
        finally:
            await self.service.stop_gdb(session_id, resume=True)

    async def crash_capture(
        self,
        elf_path: str,
        *,
        selector: DeviceSelector | None = None,
    ) -> dict[str, Any]:
        """Trigger the fixture HardFault, capture evidence, and recover."""

        resolved = await self.service.resolve_selector_wait(selector)
        info = await self.service.start_gdb(selector=resolved, elf_path=elf_path)
        session_id = str(info["session_id"])
        commands: list[CommandResult] = []
        recovered = False
        try:
            for text in (
                "-break-insert HardFault_Handler",
                "-exec-continue",
            ):
                commands.append(await self.service.gdb_command(session_id, text))
            trigger = await self.service.serial_exchange(
                selector=resolved, write="FAULT", duration=0.5
            )
            await asyncio.sleep(0.4)
            thread = await self.service.gdb_command(session_id, "-thread-info")
            commands.append(thread)
            stack = await self.service.gdb_command(session_id, "-stack-list-frames")
            commands.append(stack)
            for text in (
                "-data-list-register-values x 0 1 2 3 12 13 14 15",
                "-data-evaluate-expression $xpsr",
                "-data-read-memory-bytes $sp 128",
            ):
                commands.append(await self.service.gdb_command(session_id, text))
            # A HardFault can leave STM32H747 debug/reset state unsuitable for
            # the next one-shot Commander attach. Configure the physical reset
            # pin while this positively identified GDB lease is still active,
            # reset, and resume through J-Link before releasing the probe.
            for text in (
                '-interpreter-exec console "monitor exec SetResetType=2"',
                '-interpreter-exec console "monitor reset"',
                '-interpreter-exec console "monitor go"',
            ):
                commands.append(await self.service.gdb_command(session_id, text))
            recovered = all(item.ok for item in commands[-3:])
            stop_reasons = [
                (item.get("payload") or {}).get("reason")
                for item in thread.parsed.get("mi", [])
                if item.get("message") == "stopped"
            ]
            frames = [
                frame
                for item in stack.parsed.get("mi", [])
                for frame in (item.get("payload") or {}).get("stack", [])
            ]
            hardfault_frame = any(
                "HardFault_Handler" in str(frame.get("func", ""))
                for frame in frames
            )
            return {
                "ok": trigger.ok
                and all(item.ok for item in commands)
                and any(
                    reason in {"breakpoint-hit", "signal-received"}
                    for reason in stop_reasons
                )
                and hardfault_frame,
                "halt_reasons": stop_reasons,
                "hardfault_frame": hardfault_frame,
                "session": info,
                "trigger": trigger.model_dump(mode="json"),
                "commands": [item.model_dump(mode="json") for item in commands],
            }
        finally:
            if not recovered:
                # Recovery remains mandatory even when evidence collection
                # fails. These commands are best-effort because the original
                # exception must remain visible to the caller.
                for text in (
                    '-interpreter-exec console "monitor exec SetResetType=2"',
                    '-interpreter-exec console "monitor reset"',
                    '-interpreter-exec console "monitor go"',
                ):
                    try:
                        await self.service.gdb_command(session_id, text)
                    except Exception:
                        pass
            await self.service.stop_gdb(session_id, resume=False)
            await asyncio.sleep(1.0)

    async def compare_firmware(
        self,
        artifact_path: str,
        address: int,
        *,
        selector: DeviceSelector | None = None,
    ) -> dict[str, Any]:
        path = self.settings.resolve_allowed_path(artifact_path)
        artifact = registerable_artifact(path, kind="comparison-input")
        result = await self.service.verify_binary(
            str(path), address, selector=selector
        )
        result.artifact_hashes[str(path)] = artifact.sha256
        self.service.store.register_artifact(artifact)
        return {
            "match": result.ok,
            "artifact": artifact.model_dump(mode="json"),
            "command": result.model_dump(mode="json"),
        }

    async def compare_backup_region(
        self,
        backup_path: str,
        backup_offset: int,
        address: int,
        size: int,
        *,
        selector: DeviceSelector | None = None,
    ) -> dict[str, Any]:
        """Compare one bounded target region with a slice of a full backup."""

        source = self.settings.resolve_allowed_path(backup_path)
        if source.suffix.lower() != ".bin":
            raise ValueError("backup region comparison requires a BIN artifact")
        if backup_offset < 0 or address < 0 or size <= 0 or size > 32 * 1024 * 1024:
            raise ValueError("invalid backup region")
        if backup_offset + size > source.stat().st_size:
            raise ValueError("backup region extends beyond the artifact")
        destination = (
            self.settings.state_root
            / "artifacts"
            / f"backup-region-{backup_offset:x}-{size:x}-{uuid.uuid4()}.bin"
        )
        with source.open("rb") as handle:
            handle.seek(backup_offset)
            destination.write_bytes(handle.read(size))
        artifact = registerable_artifact(destination, kind="backup-region")
        self.service.store.register_artifact(artifact)
        result = await self.service.verify_binary(
            str(destination), address, selector=selector
        )
        result.artifact_hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        result.artifact_hashes[str(destination)] = artifact.sha256
        return {
            "match": result.ok,
            "backup_offset": backup_offset,
            "target_address": address,
            "size": size,
            "region_artifact": artifact.model_dump(mode="json"),
            "command": result.model_dump(mode="json"),
        }

    async def capture_rtt(
        self,
        elf_path: str,
        *,
        selector: DeviceSelector | None = None,
        duration_seconds: float = 3.0,
        channel: int = 0,
    ) -> dict[str, Any]:
        """Capture RTT at the ELF-derived control block into a hashed artifact."""

        if not 0.2 <= duration_seconds <= 300:
            raise ValueError("duration_seconds must be between 0.2 and 300")
        if not 0 <= channel <= 15:
            raise ValueError("RTT channel must be between 0 and 15")
        resolved = await self.service.resolve_selector_wait(selector)
        elf = self.settings.resolve_allowed_path(elf_path)
        symbols = inspect_elf(elf)["test_symbols"]
        try:
            rtt_address = int(symbols["_SEGGER_RTT"]["address"])
        except KeyError as exc:
            raise ValueError("ELF does not contain a concrete _SEGGER_RTT symbol") from exc
        destination = (
            self.settings.state_root
            / "artifacts"
            / f"rtt-{resolved.core}-{uuid.uuid4()}.log"
        )
        configuration: CommandResult | None = None
        continuation: CommandResult | None = None
        session_info: dict[str, Any] | None = None
        if channel == 0:
            # J-Link RTT Logger performs a one-shot attach that can fail when
            # sleeping Cortex-M targets require connect-under-reset. The
            # managed GDB Server keeps one positively identified connection,
            # accepts the exact ELF-derived RTT address, and exposes channel 0
            # on its bounded loopback RTT port.
            session_info = await self.service.start_gdb(
                selector=resolved, elf_path=str(elf)
            )
            session_id = str(session_info["session_id"])
            try:
                configuration = await self.service.gdb_command(
                    session_id,
                    '-interpreter-exec console '
                    f'"monitor exec SetRTTAddr=0x{rtt_address:08X}"',
                )
                if not configuration.ok:
                    raise RuntimeError("J-Link rejected the ELF-derived RTT address")
                continuation = await self.service.gdb_command(
                    session_id, "-exec-continue", timeout=2.0
                )
                if not continuation.ok:
                    raise RuntimeError("target could not be continued for RTT capture")
                await asyncio.sleep(0.5)
                result = await self.service.capture_gdb_channel(
                    session_id, "rtt", duration=duration_seconds
                )
            finally:
                await self.service.stop_gdb(session_id, resume=True)
            if result.stdout:
                destination.write_text(result.stdout, encoding="utf-8")
        else:
            # RTT Logger remains available for non-default up-channels, which
            # are not multiplexed by the GDB Server RTT Telnet endpoint.
            result = await self.service.run_application(
                "JLinkRTTLoggerExe",
                [
                    "-Device",
                    get_profile(str(resolved.target_profile))
                    .cores[str(resolved.core)]
                    .jlink_device,
                    "-If",
                    resolved.interface,
                    "-Speed",
                    str(resolved.speed_khz),
                    "-USB",
                    str(resolved.probe_serial),
                    "-RTTAddress",
                    # RTT Logger 9.62 requires SEGGER's bare-hex syntax.
                    f"{rtt_address:08X}",
                    "-RTTChannel",
                    str(channel),
                    str(destination),
                ],
                timeout=duration_seconds,
                destructive=True,
                selector=resolved,
                resume_after_preflight=True,
                resume_settle_seconds=4.0,
                attempts=2,
                retry_delay_seconds=4.0,
            )
        artifact = None
        text = ""
        if destination.exists():
            artifact = registerable_artifact(destination, kind="rtt-log")
            self.service.store.register_artifact(artifact)
            result.artifact_hashes[str(destination)] = artifact.sha256
            text = destination.read_text(encoding="utf-8", errors="replace")
            now = datetime.now(UTC)
            evidence = CommandResult(
                operation_id=str(uuid.uuid4()),
                session_id=result.session_id,
                backend="rtt-evidence",
                command=["register-rtt-artifact", str(destination)],
                started_at=now,
                finished_at=now,
                duration_ms=0,
                return_code=0,
                parsed={"rtt_address": f"0x{rtt_address:08X}", "channel": channel},
                artifact_hashes={str(destination): artifact.sha256},
                probe_identity=result.probe_identity,
                target_identity=result.target_identity,
                evidence_paths=[str(destination)],
            )
            self.service.store.append_operation(
                result=evidence,
                action="capture_rtt_evidence",
                probe_serial=resolved.probe_serial,
                destructive=False,
                request={"elf_path": str(elf), "duration_seconds": duration_seconds},
            )
        return {
            "ok": artifact is not None and artifact.size > 0,
            "expected_timeout": result.timed_out,
            "backend": "gdb-rtt" if channel == 0 else "rtt-logger",
            "rtt_address": f"0x{rtt_address:08X}",
            "channel": channel,
            "text": text,
            "command": result.model_dump(mode="json"),
            "configuration": (
                configuration.model_dump(mode="json") if configuration else None
            ),
            "continuation": (
                continuation.model_dump(mode="json") if continuation else None
            ),
            "session": session_info,
            "artifact": artifact.model_dump(mode="json") if artifact else None,
        }

    async def restore_backup(
        self,
        backup_path: str,
        address: int,
        expected_sha256: str,
        *,
        selector: DeviceSelector | None = None,
    ) -> dict[str, Any]:
        """Restore only a hash-authorized backup and verify target bytes."""

        path = self.settings.resolve_allowed_path(backup_path)
        if path.suffix.lower() != ".bin":
            raise ValueError("restore requires a raw BIN backup")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise ValueError("expected_sha256 must be a 64-character hex digest")
        if actual_hash.lower() != expected_sha256.lower():
            raise ValueError("backup hash does not match restore authorization")
        flashed = await self.flash_binary(str(path), address, selector=selector)
        compared = await self.service.verify_binary(
            str(path), address, selector=selector
        )
        return {
            "ok": flashed.ok and compared.ok,
            "backup_sha256": actual_hash,
            "flash": flashed.model_dump(mode="json"),
            "verify": compared.model_dump(mode="json"),
        }

    async def generate_validation_report(
        self, *, title: str = "J-Link MCP validation", audit_limit: int = 1000
    ) -> dict[str, Any]:
        """Persist machine- and human-readable evidence summaries."""

        run_id = str(uuid.uuid4())
        report_dir = self.settings.state_root / "reports" / run_id
        report_dir.mkdir(parents=True, exist_ok=False)
        operations = self.service.store.list_operations(limit=audit_limit)
        artifact_catalog = self.service.store.list_artifacts()
        chain_ok, chain_error = self.service.store.verify_chain()
        payload = {
            "schema_version": 1,
            "run_id": run_id,
            "title": title,
            "generated_at": datetime.now(UTC).isoformat(),
            "capabilities": self.service.capabilities().model_dump(mode="json"),
            "dependency_doctor": self.service.doctor().model_dump(mode="json"),
            "audit_chain": {"ok": chain_ok, "error": chain_error},
            "operations": operations,
            "artifacts": artifact_catalog,
        }
        machine = report_dir / "validation-report.json"
        human = report_dir / "validation-report.md"
        machine.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

        def cell(value: Any) -> str:
            text = "" if value is None else str(value)
            return text.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")

        def excerpt(value: Any, limit: int = 1200) -> str:
            text = "" if value is None else str(value).strip()
            if len(text) > limit:
                text = text[:limit] + " … [truncated; see JSON companion]"
            return cell(text)

        capabilities = payload["capabilities"]
        dependency = payload["dependency_doctor"]
        evidence_paths = sorted(
            {
                str(path)
                for operation in operations
                for path in operation["payload"].get("result", {}).get(
                    "evidence_paths", []
                )
            }
        )
        screenshot_paths = [
            path for path in evidence_paths if Path(path).suffix.lower() == ".png"
        ]
        markdown = [
            f"# {title}",
            "",
            f"- Run ID: `{run_id}`",
            f"- Generated: `{payload['generated_at']}`",
            f"- Audit chain: `{'valid' if chain_ok else 'INVALID'}`",
            f"- Dependency status: `{'PASS' if dependency['ok'] else 'FAIL'}`",
            f"- Recorded operations: `{len(operations)}`",
            f"- Registered artifacts: `{len(artifact_catalog)}`",
            f"- Screenshot evidence: `{len(screenshot_paths)}`",
            "",
            "The JSON companion is the lossless record and contains complete raw output, "
            "requests, identities, audit hashes, and structured values.",
            "",
            "## Hardware evidence",
            "",
            "| Kind | Stable serial | Model | Firmware / target | Licenses / cores |",
            "|---|---|---|---|---|",
        ]
        for probe in capabilities.get("probes", []):
            markdown.append(
                "| Probe | {serial} | {model} | {firmware} | {licenses} |".format(
                    serial=cell(probe.get("serial")),
                    model=cell(probe.get("model")),
                    firmware=cell(probe.get("firmware")),
                    licenses=cell(", ".join(probe.get("licenses", []))),
                )
            )
        for board in capabilities.get("boards", []):
            markdown.append(
                "| Board | {serial} | {model} | {target} | {cores} |".format(
                    serial=cell(board.get("serial")),
                    model=cell(board.get("model")),
                    target=cell(board.get("mcu")),
                    cores=cell(", ".join(board.get("cores", []))),
                )
            )
        markdown.extend(
            [
                "",
                "## Dependency checks",
                "",
                "| Check | Required | Result | Observed | Expected | Remediation |",
                "|---|---:|---:|---|---|---|",
            ]
        )
        for check in dependency.get("checks", []):
            markdown.append(
                "| {name} | {required} | {result} | {observed} | {expected} | {remediation} |".format(
                    name=cell(check.get("name")),
                    required="yes" if check.get("required") else "no",
                    result="PASS" if check.get("ok") else "FAIL",
                    observed=cell(check.get("observed")),
                    expected=cell(check.get("expected")),
                    remediation=cell(check.get("remediation")),
                )
            )
        markdown.extend(
            [
                "",
                "## Operation and command summary",
                "",
                "| Seq | Action | Backend | Result | Duration ms | Target state | Exact command | Warnings |",
                "|---:|---|---|---:|---:|---|---|---|",
            ]
        )
        for operation in operations:
            result = operation["payload"].get("result", {})
            target_state = (
                f"{result.get('target_state_before', 'unknown')} → "
                f"{result.get('target_state_after', 'unknown')}"
            )
            markdown.append(
                "| {sequence} | {action} | {backend} | {return_code} | {duration} | {state} | {command} | {warnings} |".format(
                    sequence=operation.get("sequence"),
                    action=cell(operation.get("action")),
                    backend=cell(result.get("backend")),
                    return_code=cell(result.get("return_code")),
                    duration=cell(result.get("duration_ms")),
                    state=cell(target_state),
                    command=cell(json.dumps(result.get("command", []))),
                    warnings=cell("; ".join(result.get("warnings", []))),
                )
            )
        markdown.extend(["", "## Recent log excerpts", ""])
        logged = 0
        for operation in operations:
            result = operation["payload"].get("result", {})
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            if not stdout and not stderr:
                continue
            markdown.extend(
                [
                    f"### Sequence {operation.get('sequence')}: {cell(operation.get('action'))}",
                    "",
                    f"- stdout: {excerpt(stdout)}",
                    f"- stderr: {excerpt(stderr)}",
                    "",
                ]
            )
            logged += 1
            if logged >= 25:
                break
        markdown.extend(
            [
                "## Artifact hashes",
                "",
                "| Kind | Size | SHA-256 | Path |",
                "|---|---:|---|---|",
            ]
        )
        for artifact in artifact_catalog:
            markdown.append(
                "| {kind} | {size} | `{sha256}` | {path} |".format(
                    kind=cell(artifact.get("kind")),
                    size=cell(artifact.get("size")),
                    sha256=cell(artifact.get("sha256")),
                    path=cell(artifact.get("path")),
                )
            )
        markdown.extend(["", "## Evidence paths", ""])
        markdown.extend(f"- {cell(path)}" for path in evidence_paths)
        markdown.append("")
        human.write_text("\n".join(markdown), encoding="utf-8")
        artifacts = [
            registerable_artifact(machine, kind="validation-report-json"),
            registerable_artifact(human, kind="validation-report-markdown"),
        ]
        for artifact in artifacts:
            self.service.store.register_artifact(artifact)
        return {
            "run_id": run_id,
            "audit_chain_ok": chain_ok,
            "artifacts": [item.model_dump(mode="json") for item in artifacts],
        }

    async def validate_fixture(
        self,
        *,
        selector: DeviceSelector | None = None,
        m7_sketch: str = "firmware/giga_hil/m7",
        m4_sketch: str = "firmware/giga_hil/m4",
    ) -> ValidationReport:
        resolved = await self.service.resolve_selector_wait(selector)
        report_selector = DeviceSelector.model_validate(
            resolved.model_dump(mode="python")
        )
        run_id = str(uuid.uuid4())
        started = datetime.now(UTC)
        steps: list[ValidationStep] = []
        artifacts: list[Artifact] = []
        warnings: list[str] = []
        restored = False
        backup: Artifact | None = None
        m7_access = resolved.model_copy(update={"core": TargetCore.M7})

        preflight = await self.hardware_preflight(selector=resolved)
        steps.append(
            ValidationStep(
                name="hardware_preflight",
                ok=bool(preflight["ok"]),
                details=preflight,
            )
        )
        if not preflight["ok"]:
            warnings.append("validation stopped after failed non-destructive preflight")
            return ValidationReport(
                run_id=run_id,
                started_at=started,
                finished_at=datetime.now(UTC),
                selector=report_selector,
                steps=steps,
                warnings=warnings,
            )

        if not self.config.test_target_disposable:
            backup_result, backup = await self.backup_flash(
                0x08000000, 0x200000, selector=m7_access
            )
            steps.append(
                ValidationStep(
                    name="backup_original_flash",
                    ok=backup_result.ok and backup is not None,
                    operation_id=backup_result.operation_id,
                    details=(
                        backup.model_dump(mode="json") if backup else backup_result.parsed
                    ),
                    evidence_paths=backup_result.evidence_paths,
                )
            )
            if not backup:
                warnings.append(
                    "Original flash could not be backed up and the target is not designated disposable."
                )
                return ValidationReport(
                    run_id=run_id,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    selector=report_selector,
                    steps=steps,
                    warnings=warnings,
                )
            artifacts.append(backup)

        try:
            deploy = await self.dual_core_deploy(
                selector=resolved, m7_sketch=m7_sketch, m4_sketch=m4_sketch
            )
            steps.append(
                ValidationStep(name="dual_core_deploy", ok=deploy["ok"], details=deploy)
            )
            for key in ("m4_build", "m7_build"):
                artifacts.extend(
                    Artifact.model_validate(item) for item in deploy[key]["artifacts"]
                )
            m7_elf = next(
                item.path
                for item in artifacts
                if item.kind == "elf" and "/m7-" in item.path
            )
            m4_elf = next(
                item.path
                for item in artifacts
                if item.kind == "elf" and "/m4-" in item.path
            )
            observe = await self.boot_and_observe(
                selector=resolved, m7_elf_path=m7_elf, m4_elf_path=m4_elf
            )
            steps.append(
                ValidationStep(name="boot_and_observe", ok=observe["ok"], details=observe)
            )
            debug = await self.debug_fixture(m7_elf, selector=resolved)
            steps.append(
                ValidationStep(name="debug_assertions", ok=debug["ok"], details=debug)
            )
            crash = await self.crash_capture(m7_elf, selector=resolved)
            steps.append(
                ValidationStep(name="controlled_crash", ok=crash["ok"], details=crash)
            )
        except Exception as exc:
            steps.append(
                ValidationStep(
                    name="validation_exception",
                    ok=False,
                    details={"type": type(exc).__name__, "message": str(exc)},
                )
            )
        finally:
            if backup:
                try:
                    restore = await self.restore_backup(
                        backup.path,
                        0x08000000,
                        backup.sha256,
                        selector=m7_access,
                    )
                    restored = bool(restore["ok"])
                    steps.append(
                        ValidationStep(
                            name="restore_original_flash", ok=restored, details=restore
                        )
                    )
                except Exception as exc:
                    steps.append(
                        ValidationStep(
                            name="restore_original_flash",
                            ok=False,
                            details={"type": type(exc).__name__, "message": str(exc)},
                        )
                    )

        report = await self.generate_validation_report(
            title=f"GIGA fixture validation {run_id}"
        )
        artifacts.extend(Artifact.model_validate(item) for item in report["artifacts"])
        steps.append(
            ValidationStep(
                name="validation_report",
                ok=bool(report["audit_chain_ok"]),
                details=report,
                evidence_paths=[item["path"] for item in report["artifacts"]],
            )
        )
        return ValidationReport(
            run_id=run_id,
            started_at=started,
            finished_at=datetime.now(UTC),
            selector=report_selector,
            steps=steps,
            artifacts=artifacts,
            restored_original=restored,
            warnings=warnings,
        )
