from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from jlink_mcp_giga_protocol_bridge.extension import (
    GigaProtocolBridgeExtension,
    _dependencies,
)

from jlink_mcp.extensions.api import ExtensionError
from jlink_mcp.extensions.loader import ExtensionManager


class EntryPoint:
    name = "giga_protocol_bridge"

    @staticmethod
    def load():
        return GigaProtocolBridgeExtension


def test_bridge_cannot_load_without_arduino_giga(settings) -> None:
    from jlink_mcp.extensions.api import (
        ArtifactService,
        ExtensionRegistry,
        ExtensionServices,
    )

    manager = ExtensionManager(
        enabled=["giga_protocol_bridge"],
        config_path=None,
        services=ExtensionServices(
            jlink=object(),
            serial=object(),
            artifacts=ArtifactService(settings, object()),
            audit=object(),
            paths=settings,
            process=object(),
        ),
        registry=ExtensionRegistry(),
        mcp=object(),
        entry_points=[EntryPoint()],
        environ={},
    )
    with pytest.raises(ExtensionError, match="disabled or missing dependencies"):
        manager.load()


def test_packaged_release_retains_authorizing_checksum(tmp_path: Path) -> None:
    checks = {item.name: item for item in _dependencies(tmp_path)}
    assert checks["protocol-bridge-release"].ok


def test_release_doctor_requires_exact_manifest_authorization(tmp_path: Path) -> None:
    firmware_root = tmp_path / "protocol_bridge"
    release = firmware_root / "release"
    release.mkdir(parents=True)
    hex_path = release / "protocol_bridge_m7.hex"
    manifest_path = release / "protocol_bridge_manifest.json"
    checksum_path = release / "SHA256SUMS"
    hex_path.write_bytes(b":fixture\n")
    manifest_path.write_text('{"fixture":true}\n', encoding="utf-8")
    hex_sha = hashlib.sha256(hex_path.read_bytes()).hexdigest()
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    def release_ok() -> bool:
        checks = {
            item.name: item
            for item in _dependencies(tmp_path, firmware_root=firmware_root)
        }
        return checks["protocol-bridge-release"].ok

    checksum_path.write_text(
        f"{hex_sha}  {hex_path.name}\n{manifest_sha}  {manifest_path.name}\n",
        encoding="utf-8",
    )
    assert release_ok()

    checksum_path.write_text(f"{hex_sha}  {hex_path.name}\n", encoding="utf-8")
    assert not release_ok()

    checksum_path.write_text(
        f"{hex_sha}  {hex_path.name}\n{'0' * 64}  {manifest_path.name}\n",
        encoding="utf-8",
    )
    assert not release_ok()

    checksum_path.write_text(
        f"{hex_sha}  {hex_path.name}\n"
        f"{manifest_sha}  {manifest_path.name}\n"
        f"{hex_sha}  unexpected.bin\n",
        encoding="utf-8",
    )
    assert not release_ok()
