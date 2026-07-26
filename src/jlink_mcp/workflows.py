"""Target-neutral flash, backup, comparison, RTT, and reporting workflows."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import inspect_elf, registerable_artifact
from .models import Artifact, CommandResult, DeviceSelector
from .service import JLinkService


class Workflows:
    def __init__(self, service: JLinkService) -> None:
        self.service = service
        self.settings = service.settings

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

    async def compare_firmware(
        self,
        artifact_path: str,
        address: int,
        *,
        selector: DeviceSelector | None = None,
    ) -> dict[str, Any]:
        path = self.settings.resolve_allowed_path(artifact_path)
        artifact = registerable_artifact(path, kind="comparison-input")
        result = await self.service.verify_binary(str(path), address, selector=selector)
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
        result.artifact_hashes[str(source)] = hashlib.sha256(
            source.read_bytes()
        ).hexdigest()
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
            raise ValueError(
                "ELF does not contain a concrete _SEGGER_RTT symbol"
            ) from exc
        destination = (
            self.settings.state_root
            / "artifacts"
            / f"rtt-{resolved.core or 'target'}-{uuid.uuid4()}.log"
        )
        configuration: CommandResult | None = None
        continuation: CommandResult | None = None
        session_info: dict[str, Any] | None = None
        if channel == 0:
            session_info = await self.service.start_gdb(
                selector=resolved, elf_path=str(elf)
            )
            session_id = str(session_info["session_id"])
            try:
                configuration = await self.service.gdb_command(
                    session_id,
                    "-interpreter-exec console "
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
            result = await self.service.run_application(
                "JLinkRTTLoggerExe",
                [
                    "-Device",
                    self.service.extensions.targets.jlink_device(
                        resolved.target_profile, resolved.core
                    ),
                    "-If",
                    resolved.interface,
                    "-Speed",
                    str(resolved.speed_khz),
                    "-USB",
                    str(resolved.probe_serial),
                    "-RTTAddress",
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
                for path in operation["payload"]
                .get("result", {})
                .get("evidence_paths", [])
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
            (
                "The JSON companion is the lossless record and contains complete raw output, "
                "requests, identities, audit hashes, and structured values."
            ),
            "",
            "## Target evidence",
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
