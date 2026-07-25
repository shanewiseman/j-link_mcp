from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from jlink_mcp_arduino_giga.extension import ArduinoGigaExtension
from jlink_mcp_arduino_giga.profiles import GIGA_R1
from jlink_mcp_giga_protocol_bridge.extension import (
    GigaProtocolBridgeExtension,
    _dependencies,
)

from jlink_mcp.extensions.api import (
    ArtifactService,
    ExtensionError,
    ExtensionRegistry,
    ExtensionServices,
)
from jlink_mcp.extensions.loader import ExtensionManager
from jlink_mcp.service import JLinkService


class EntryPoint:
    name = "giga_protocol_bridge"

    @staticmethod
    def load():
        return GigaProtocolBridgeExtension


class ArduinoEntryPoint:
    name = "arduino_giga"

    @staticmethod
    def load():
        return ArduinoGigaExtension


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}
        self.resources: dict[str, Any] = {}

    def tool(self, *, name: str, annotations: Any = None):
        def decorate(function):
            self.tools[name] = function
            return function

        return decorate

    def resource(self, uri: str, *, name: str | None, mime_type: str | None):
        def decorate(function):
            self.resources[uri] = function
            return function

        return decorate


def test_bridge_cannot_load_without_arduino_giga(settings) -> None:
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


def test_bridge_consumes_giga_profile_through_public_service(settings) -> None:
    registry = ExtensionRegistry()
    service = JLinkService(settings, registry)
    manager = ExtensionManager(
        enabled=["arduino_giga", "giga_protocol_bridge"],
        config_path=None,
        services=ExtensionServices(
            jlink=service,
            serial=service.serial,
            artifacts=ArtifactService(settings, service.store),
            audit=service.store,
            paths=settings,
            process=service.runner,
        ),
        registry=registry,
        mcp=FakeMCP(),
        entry_points=[ArduinoEntryPoint(), EntryPoint()],
        environ={},
    )

    manager.load()

    assert manager.loaded_ids == ["arduino_giga", "giga_protocol_bridge"]
    assert registry._services[("arduino_giga", "profile")] is GIGA_R1
    bridge = registry._services[("giga_protocol_bridge", "bridge")]
    workflows = registry._services[("giga_protocol_bridge", "workflows")]
    assert bridge.target_profile is GIGA_R1
    assert workflows.target_profile is GIGA_R1


def test_bridge_runtime_has_no_private_arduino_giga_imports() -> None:
    runtime_root = Path(__file__).resolve().parents[1] / "src"
    for source in runtime_root.rglob("*.py"):
        assert "jlink_mcp_arduino_giga" not in source.read_text(encoding="utf-8")


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
