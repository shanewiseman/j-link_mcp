from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from conftest import make_result
from test_service import SAMPLE_PROFILE, selector

import jlink_mcp.workflows as workflow_module
from jlink_mcp.extensions.api import ExtensionRegistry
from jlink_mcp.service import JLinkService
from jlink_mcp.workflows import Workflows


@pytest.fixture
def workflow(settings, manifest, monkeypatch):
    registry = ExtensionRegistry()
    registry.targets.register_profile(SAMPLE_PROFILE)
    service = JLinkService(settings, registry)
    monkeypatch.setattr(service, "capabilities", lambda: copy.deepcopy(manifest))
    return Workflows(service)


@pytest.mark.asyncio
async def test_flash_backup_compare_and_restore(workflow, monkeypatch) -> None:
    commands: list[tuple[list[str], str]] = []

    async def commander(items, **kwargs):
        commands.append((list(items), kwargs["action"]))
        if kwargs["action"] == "backup_flash":
            destination = Path(str(items[0]).split('"')[1])
            destination.write_bytes(b"original")
        return make_result(parsed={"flash_verified": True})

    async def verify(path, address, **kwargs):
        return make_result(parsed={"flash_verified": True})

    monkeypatch.setattr(workflow.service, "commander_commands", commander)
    monkeypatch.setattr(workflow.service, "verify_binary", verify)
    image = workflow.settings.workspace_root / "image.hex"
    image.write_text(":00000001FF\n", encoding="utf-8")
    raw = workflow.settings.workspace_root / "image.bin"
    raw.write_bytes(b"original")

    flashed = await workflow.flash_and_verify(str(image), selector=selector())
    binary = await workflow.flash_binary(str(raw), 0x1000, selector=selector())
    backup_result, backup = await workflow.backup_flash(
        0x1000, len(b"original"), selector=selector()
    )
    compared = await workflow.compare_firmware(str(raw), 0x1000, selector=selector())
    restored = await workflow.restore_backup(
        str(raw),
        0x1000,
        hashlib.sha256(raw.read_bytes()).hexdigest(),
        selector=selector(),
    )

    assert flashed.ok and binary.ok and backup_result.ok
    assert backup is not None and backup.sha256
    assert compared["match"] and restored["ok"]
    assert {action for _, action in commands} >= {
        "flash_and_verify",
        "flash_binary",
        "backup_flash",
    }


@pytest.mark.asyncio
async def test_rtt_capture_registers_evidence(workflow, monkeypatch) -> None:
    elf = workflow.settings.workspace_root / "firmware.elf"
    elf.write_bytes(b"ELF")
    monkeypatch.setattr(
        workflow_module,
        "inspect_elf",
        lambda path: {"test_symbols": {"_SEGGER_RTT": {"address": 0x20000000}}},
    )

    async def application(name, args, **kwargs):
        Path(args[-1]).write_text("RTT evidence\n", encoding="utf-8")
        return make_result(timed_out=True)

    monkeypatch.setattr(workflow.service, "run_application", application)
    result = await workflow.capture_rtt(
        str(elf), selector=selector(), duration_seconds=0.2, channel=1
    )
    assert result["ok"]
    assert result["expected_timeout"]
    assert result["artifact"]["sha256"]
    assert workflow.service.store.verify_chain() == (True, None)


@pytest.mark.asyncio
async def test_validation_report_persists_json_markdown_and_chain(workflow) -> None:
    report = await workflow.generate_validation_report(title="Core validation")
    assert report["audit_chain_ok"]
    assert {item["kind"] for item in report["artifacts"]} == {
        "validation-report-json",
        "validation-report-markdown",
    }
    assert all(Path(item["path"]).is_file() for item in report["artifacts"])
