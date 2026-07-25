"""Deterministic bridge release and deployment workflows."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from jlink_mcp.artifacts import registerable_artifact
from jlink_mcp.models import Artifact
from jlink_mcp.profiles import TargetProfile

from .models import (
    BRIDGE_FIRMWARE_VERSION,
    BRIDGE_WIRE_VERSION,
    DeviceSelector,
    ProtocolBridgeDeployResult,
    ProtocolBridgeReleaseResult,
)
from .service import ProtocolBridgeService

_BRIDGE_RELEASE_EPOCH = 1784937600
_BRIDGE_RELEASE_TIMESTAMP = "2026-07-25T00:00:00Z"


def _release_checksums_authorize(
    checksum_path: Path, artifacts: tuple[Path, ...]
) -> bool:
    try:
        expected = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in artifacts
        }
        lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    authorized: dict[str, str] = {}
    for line in lines:
        fields = line.split()
        if len(fields) != 2:
            return False
        digest, filename = fields
        if (
            len(digest) != 64
            or filename != Path(filename).name
            or filename in authorized
            or any(character not in "0123456789abcdefABCDEF" for character in digest)
        ):
            return False
        authorized[filename] = digest.lower()
    return authorized == expected


class ProtocolBridgeDeployError(RuntimeError):
    """Deployment failure with an authorized backup and recovery outcome."""

    def __init__(
        self,
        reason: str,
        *,
        backup: Artifact,
        restore: dict[str, object] | None,
        restore_error: str | None = None,
    ) -> None:
        self.backup = backup
        self.restore = restore
        self.restore_error = restore_error
        if restore and restore.get("ok"):
            outcome = "original flash restoration verified"
        elif restore_error:
            outcome = f"original flash restoration raised: {restore_error}"
        else:
            outcome = "original flash restoration failed verification"
        super().__init__(
            f"{reason}; {outcome}; authorized backup path={backup.path} "
            f"sha256={backup.sha256}"
        )


class ProtocolBridgeWorkflows:
    def __init__(
        self,
        service,
        bridge: ProtocolBridgeService,
        giga_workflows: Any,
        giga_config: Any,
        target_profile: TargetProfile,
    ) -> None:
        self.service = service
        self.settings = service.settings
        self.bridge = bridge
        self.giga_workflows = giga_workflows
        self.giga_config = giga_config
        self.target_profile = target_profile

    @staticmethod
    def _firmware_root() -> Path:
        return Path(__file__).parent / "firmware" / "protocol_bridge"

    @staticmethod
    def _protocol_bridge_source_sha256(source: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(source.rglob("*")):
            if not path.is_file() or "release" in path.relative_to(source).parts:
                continue
            if path.name == "BridgeBuildIdentity.generated.h":
                continue
            relative = path.relative_to(source).as_posix()
            digest.update(relative.encode("utf-8") + b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _stage_authorized_release(self, source: Path, expected_sha256: str) -> Path:
        """Copy a packaged release into the path-confined persistent state root."""

        destination = (
            self.settings.state_root
            / "artifacts"
            / "protocol-bridge-releases"
            / expected_sha256
            / source.name
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file():
            observed = hashlib.sha256(destination.read_bytes()).hexdigest()
            if observed != expected_sha256:
                raise ValueError("staged protocol bridge release hash mismatch")
            return destination

        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4()}.tmp")
        try:
            shutil.copyfile(source, temporary)
            observed = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if observed != expected_sha256:
                raise ValueError("staged protocol bridge release hash mismatch")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    async def build_protocol_bridge_release(
        self,
        *,
        verify_checked_in: bool = True,
    ) -> ProtocolBridgeReleaseResult:
        """Build a deterministic state bundle and compare its HEX byte-for-byte."""

        source = self._firmware_root().resolve(strict=True)
        source_sha256 = self._protocol_bridge_source_sha256(source)
        build_root = (
            self.settings.state_root
            / "artifacts"
            / "protocol-bridge-builds"
            / f"{source_sha256[:16]}-{uuid.uuid4()}"
        )
        staged = build_root / "source" / "protocol_bridge"
        output = build_root / "output"
        bundle = build_root / "release"
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("release"))
        output.mkdir(parents=True)
        bundle.mkdir(parents=True)
        build_id = f"protocol-bridge-{BRIDGE_FIRMWARE_VERSION}-{source_sha256[:12]}"
        (staged / "BridgeBuildIdentity.generated.h").write_text(
            "#pragma once\n"
            f'#define BRIDGE_FIRMWARE_VERSION "{BRIDGE_FIRMWARE_VERSION}"\n'
            f"#define BRIDGE_WIRE_VERSION {BRIDGE_WIRE_VERSION}\n"
            f'#define BRIDGE_BUILD_ID "{build_id}"\n'
            f'#define BRIDGE_BUILD_TIMESTAMP "{_BRIDGE_RELEASE_TIMESTAMP}"\n'
            f'#define BRIDGE_SOURCE_SHA256 "{source_sha256}"\n',
            encoding="utf-8",
        )
        command = await self.service.runner.run(
            [
                self.giga_config.arduino_cli,
                "compile",
                "--fqbn",
                self.giga_config.fqbn,
                "--board-options",
                "target_core=cm7,split=75_25",
                "--output-dir",
                output,
                "--export-binaries",
                "--clean",
                staged,
            ],
            backend="arduino-cli-protocol-bridge-release",
            cwd=self.settings.workspace_root,
            env={
                "SOURCE_DATE_EPOCH": str(_BRIDGE_RELEASE_EPOCH),
                "TZ": "UTC",
                "LC_ALL": "C.UTF-8",
            },
            timeout=1200,
        )
        audit_request = {
            "source": str(source),
            "source_sha256": source_sha256,
            "release_epoch": _BRIDGE_RELEASE_EPOCH,
            "fqbn": self.giga_config.fqbn,
            "board_options": "target_core=cm7,split=75_25",
        }
        artifacts: list[Artifact] = []
        checked_in_hex = source / "release" / "protocol_bridge_m7.hex"
        reproducible = False
        if command.ok:
            generated_hex = next(
                (
                    path
                    for path in sorted(output.glob("*.hex"))
                    if ".with_bootloader." not in path.name
                ),
                None,
            )
            if generated_hex is None:
                raise RuntimeError(
                    "Arduino build succeeded without an application-only HEX image"
                )
            release_hex = bundle / "protocol_bridge_m7.hex"
            shutil.copy2(generated_hex, release_hex)
            for path in sorted(output.iterdir()):
                if ".with_bootloader." in path.name:
                    continue
                if path.is_file() and path.suffix.lower() in {
                    ".elf",
                    ".hex",
                    ".bin",
                    ".map",
                }:
                    artifact = registerable_artifact(
                        path, kind=f"protocol-bridge-{path.suffix[1:].lower()}"
                    )
                    self.service.store.register_artifact(artifact)
                    artifacts.append(artifact)
            hex_artifact = registerable_artifact(
                release_hex, kind="protocol-bridge-release-hex"
            )
            hex_artifact.metadata.update(
                {
                    "firmware_version": BRIDGE_FIRMWARE_VERSION,
                    "wire_version": BRIDGE_WIRE_VERSION,
                    "source_sha256": source_sha256,
                    "build_id": build_id,
                }
            )
            self.service.store.register_artifact(hex_artifact)
            artifacts.append(hex_artifact)
            manifest_path = bundle / "protocol_bridge_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "firmware_version": BRIDGE_FIRMWARE_VERSION,
                        "wire_version": BRIDGE_WIRE_VERSION,
                        "build_id": build_id,
                        "build_timestamp": _BRIDGE_RELEASE_TIMESTAMP,
                        "source_date_epoch": _BRIDGE_RELEASE_EPOCH,
                        "source_sha256": source_sha256,
                        "fqbn": self.giga_config.fqbn,
                        "board_options": "target_core=cm7,split=75_25",
                        "arduino_cli": "1.5.1",
                        "arduino_core": "arduino:mbed_giga@4.6.0",
                        "libraries": {
                            "Arduino_USBHostMbed5": "0.3.1",
                            "ArduinoBLE": "2.1.0",
                            "Arduino_SpiNINA": "0.0.2",
                        },
                        "hex": {
                            "filename": release_hex.name,
                            "sha256": hex_artifact.sha256,
                            "size": hex_artifact.size,
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            manifest_artifact = registerable_artifact(
                manifest_path, kind="protocol-bridge-release-manifest"
            )
            self.service.store.register_artifact(manifest_artifact)
            artifacts.append(manifest_artifact)
            checksums_path = bundle / "SHA256SUMS"
            checksums_path.write_text(
                f"{hex_artifact.sha256}  {release_hex.name}\n"
                f"{manifest_artifact.sha256}  {manifest_path.name}\n",
                encoding="utf-8",
            )
            checksums_artifact = registerable_artifact(
                checksums_path, kind="protocol-bridge-release-checksums"
            )
            self.service.store.register_artifact(checksums_artifact)
            artifacts.append(checksums_artifact)
            reproducible = checked_in_hex.is_file() and (
                checked_in_hex.read_bytes() == release_hex.read_bytes()
            )
            command.parsed.update(
                {
                    "source_sha256": source_sha256,
                    "generated_hex": str(release_hex),
                    "checked_in_hex": str(checked_in_hex),
                    "reproducible": reproducible,
                }
            )
            if verify_checked_in and not reproducible:
                command.return_code = 1
                command.stderr = (
                    "generated protocol bridge HEX differs from the checked-in release"
                )
        self.service.store.append_operation(
            result=command,
            action="build_protocol_bridge_release",
            probe_serial=None,
            destructive=False,
            request=audit_request,
        )
        return ProtocolBridgeReleaseResult(
            source_sha256=source_sha256,
            build_directory=str(build_root),
            command=command,
            artifacts=artifacts,
            checked_in_hex=str(checked_in_hex),
            reproducible=reproducible,
        )

    async def deploy_protocol_bridge(
        self,
        *,
        selector: DeviceSelector | None = None,
    ) -> ProtocolBridgeDeployResult:
        """Back up all internal flash, deploy the release HEX, and handshake."""

        resolved = await self.service.resolve_selector_wait(selector)
        if (
            resolved.target_profile != self.target_profile.id
            or resolved.core != self.target_profile.default_core
        ):
            raise ValueError("the protocol bridge firmware runs on the GIGA M7")
        release = self._firmware_root().resolve(strict=True) / "release"
        hex_path = release / "protocol_bridge_m7.hex"
        manifest_path = release / "protocol_bridge_manifest.json"
        checksums_path = release / "SHA256SUMS"
        for path in (hex_path, manifest_path, checksums_path):
            if not path.is_file():
                raise FileNotFoundError(
                    f"protocol bridge release file is missing: {path}"
                )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_source = self._protocol_bridge_source_sha256(release.parent)
        expected_hex = hashlib.sha256(hex_path.read_bytes()).hexdigest()
        if manifest.get("source_sha256") != expected_source:
            raise ValueError("protocol bridge release source hash is stale")
        if manifest.get("hex", {}).get("sha256") != expected_hex:
            raise ValueError(
                "protocol bridge release HEX hash does not match its manifest"
            )
        if not _release_checksums_authorize(checksums_path, (hex_path, manifest_path)):
            raise ValueError(
                "protocol bridge SHA256SUMS does not authorize the release"
            )
        staged_hex = self._stage_authorized_release(hex_path, expected_hex)

        preflight = await self.giga_workflows.hardware_preflight(
            selector=resolved, prepare_dual_core=True
        )
        if not preflight["ok"]:
            raise RuntimeError(
                "protocol bridge deployment stopped after failed preflight"
            )
        assert resolved.probe_serial is not None
        async with self.service.leases.lease(
            resolved.probe_serial,
            owner="deploy_protocol_bridge",
            timeout=max(180.0, self.settings.default_timeout_seconds),
        ):
            backup_result, backup = await self.giga_workflows.backup_flash(
                0x08000000, 0x200000, selector=resolved
            )
            if not backup_result.ok or backup is None:
                raise RuntimeError(
                    "protocol bridge deployment requires a readable flash backup"
                )
            try:
                flash = await self.giga_workflows.flash_and_verify(
                    str(staged_hex), selector=resolved
                )
                if not flash.ok:
                    raise RuntimeError("protocol bridge flashing failed")
                firmware = registerable_artifact(
                    staged_hex, kind="protocol-bridge-release-hex"
                )
                firmware.metadata.update(manifest)
                self.service.store.register_artifact(firmware)
                handshake = await self.bridge.status(selector=resolved)
                if (
                    handshake.firmware_version != manifest["firmware_version"]
                    or handshake.wire_version != manifest["wire_version"]
                    or handshake.source_sha256 != manifest["source_sha256"]
                ):
                    raise RuntimeError(
                        "flashed protocol bridge identity does not match the release"
                    )
            # Recovery stays inside the transaction lease, even on cancellation.
            except BaseException as exc:
                restore: dict[str, object] | None = None
                restore_error: str | None = None
                try:
                    restore = await self.giga_workflows.restore_backup(
                        backup.path,
                        0x08000000,
                        backup.sha256,
                        selector=resolved,
                    )
                except BaseException as recovery_exc:  # noqa: BLE001
                    # Preserve the authorized backup even if cancellation recurs.
                    restore_error = f"{type(recovery_exc).__name__}: {recovery_exc}"
                raise ProtocolBridgeDeployError(
                    str(exc),
                    backup=backup,
                    restore=restore,
                    restore_error=restore_error,
                ) from exc
            return ProtocolBridgeDeployResult(
                selector=DeviceSelector.model_validate(
                    resolved.model_dump(mode="python")
                ),
                preflight=preflight,
                backup=backup,
                firmware=firmware,
                flash=flash,
                handshake=handshake,
            )
