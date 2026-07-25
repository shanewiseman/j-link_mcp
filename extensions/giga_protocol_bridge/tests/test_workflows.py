from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jlink_mcp.extensions.api import ExtensionRegistry
from jlink_mcp.models import Artifact, DeviceSelector
from jlink_mcp.service import JLinkService
from jlink_mcp_arduino_giga.profiles import GIGA_R1
from jlink_mcp_arduino_giga.workflows import ArduinoGigaWorkflows
from jlink_mcp_giga_protocol_bridge.backend import ProtocolBridgeBackend
from jlink_mcp_giga_protocol_bridge.config import GigaProtocolBridgeConfig
from jlink_mcp_giga_protocol_bridge.models import ProtocolBridgeStatus
from jlink_mcp_giga_protocol_bridge.service import ProtocolBridgeService
from jlink_mcp_giga_protocol_bridge.workflows import ProtocolBridgeWorkflows

from .conftest import make_result


@pytest.fixture
def workflow(settings, giga_config):
    registry = ExtensionRegistry()
    registry.targets.register_profile(GIGA_R1)
    service = JLinkService(settings, registry)
    giga = ArduinoGigaWorkflows(service, giga_config)
    bridge = ProtocolBridgeService(
        service,
        ProtocolBridgeBackend(service.serial),
        GigaProtocolBridgeConfig(),
    )
    return ProtocolBridgeWorkflows(service, bridge, giga, giga_config)


def selector(core: str = "m7") -> DeviceSelector:
    return DeviceSelector(
        probe_serial="000802008248",
        board_serial="0045002B3333511632363530",
        target_profile=GIGA_R1.id,
        core=core,
    )


@pytest.mark.asyncio
async def test_release_is_reproducible_and_fully_audited(
    workflow, monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "protocol_bridge"
    source.mkdir()
    (source / "protocol_bridge.ino").write_text(
        "void setup(){} void loop(){}\n", encoding="utf-8"
    )
    (source / "BridgeWire.h").write_text("#pragma once\n", encoding="utf-8")
    release = source / "release"
    release.mkdir()
    monkeypatch.setattr(workflow, "_firmware_root", lambda: source)

    async def compile_bridge(argv, **kwargs):
        assert kwargs["env"] == {
            "SOURCE_DATE_EPOCH": "1784937600",
            "TZ": "UTC",
            "LC_ALL": "C.UTF-8",
        }
        output = Path(argv[argv.index("--output-dir") + 1])
        (output / "protocol_bridge.ino.hex").write_bytes(b":deterministic-hex\n")
        (output / "protocol_bridge.ino.with_bootloader.hex").write_bytes(
            b":must-not-be-released\n"
        )
        (output / "protocol_bridge.ino.elf").write_bytes(b"ELF")
        (output / "protocol_bridge.ino.bin").write_bytes(b"BIN")
        (output / "protocol_bridge.ino.map").write_text("MAP", encoding="utf-8")
        return make_result(backend="arduino-cli-protocol-bridge-release")

    monkeypatch.setattr(workflow.service.runner, "run", compile_bridge)
    first = await workflow.build_protocol_bridge_release(verify_checked_in=False)
    assert first.command.ok and not first.reproducible
    generated = Path(first.build_directory) / "release"
    checked_in = release / "protocol_bridge_m7.hex"
    checked_in.write_bytes((generated / "protocol_bridge_m7.hex").read_bytes())
    second = await workflow.build_protocol_bridge_release(verify_checked_in=True)
    assert second.command.ok and second.reproducible
    manifest = json.loads(
        (
            Path(second.build_directory)
            / "release"
            / "protocol_bridge_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["source_date_epoch"] == 1784937600
    assert all("with_bootloader" not in item.path for item in second.artifacts)
    audit = workflow.service.store.list_operations(limit=1)[0]
    assert audit["action"] == "build_protocol_bridge_release"
    assert audit["payload"]["result"]["parsed"]["reproducible"] is True


@pytest.mark.asyncio
async def test_deploy_requires_backup_before_flash(
    workflow, monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "protocol_bridge"
    release = source / "release"
    release.mkdir(parents=True)
    (source / "protocol_bridge.ino").write_text("source\n", encoding="utf-8")
    firmware = release / "protocol_bridge_m7.hex"
    firmware.write_bytes(b":bridge\n")
    source_sha = workflow._protocol_bridge_source_sha256(source)
    hex_sha = hashlib.sha256(firmware.read_bytes()).hexdigest()
    manifest = {
        "firmware_version": "1.0.0",
        "wire_version": 1,
        "source_sha256": source_sha,
        "hex": {"sha256": hex_sha},
    }
    (release / "protocol_bridge_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (release / "SHA256SUMS").write_text(
        f"{hex_sha}  protocol_bridge_m7.hex\n", encoding="utf-8"
    )
    monkeypatch.setattr(workflow, "_firmware_root", lambda: source)
    backup_path = workflow.settings.state_root / "artifacts" / "original.bin"
    backup_path.write_bytes(b"original")
    backup = Artifact.from_path(
        backup_path,
        kind="flash-backup",
        sha256=hashlib.sha256(backup_path.read_bytes()).hexdigest(),
    )
    events: list[str] = []

    async def resolve(value):
        return selector()

    async def preflight(**kwargs):
        events.append(f"preflight:{kwargs.get('prepare_dual_core')}")
        return {"ok": True}

    async def backup_flash(address, size, **kwargs):
        events.append(f"backup:{address:#x}:{size:#x}")
        return make_result(), backup

    async def flash(path, **kwargs):
        events.append(f"flash:{Path(path).name}")
        return make_result(parsed={"flash_verified": True})

    async def status(**kwargs):
        events.append("handshake")
        return ProtocolBridgeStatus(
            firmware_version="1.0.0",
            wire_version=1,
            build_id="fixture",
            source_sha256=source_sha,
            supported_interfaces=["spi"],
            safe_pins=["D22"],
            transfer_limits={"application_payload": 64000},
            command=make_result(),
        )

    monkeypatch.setattr(workflow.service, "resolve_selector_wait", resolve)
    monkeypatch.setattr(workflow.giga_workflows, "hardware_preflight", preflight)
    monkeypatch.setattr(workflow.giga_workflows, "backup_flash", backup_flash)
    monkeypatch.setattr(workflow.giga_workflows, "flash_and_verify", flash)
    monkeypatch.setattr(workflow.bridge, "status", status)
    deployed = await workflow.deploy_protocol_bridge(selector=selector())
    assert deployed.ok
    assert events == [
        "preflight:True",
        "backup:0x8000000:0x200000",
        "flash:protocol_bridge_m7.hex",
        "handshake",
    ]

    async def failed_backup(*args, **kwargs):
        events.append("backup-failed")
        return make_result(return_code=1), None

    events.clear()
    monkeypatch.setattr(workflow.giga_workflows, "backup_flash", failed_backup)
    with pytest.raises(RuntimeError, match="requires a readable flash backup"):
        await workflow.deploy_protocol_bridge(selector=selector())
    assert events == ["preflight:True", "backup-failed"]
