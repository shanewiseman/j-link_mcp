from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import make_result
from pydantic import ValidationError

from jlink_mcp.config import Settings, _first_existing
from jlink_mcp.leases import ProbeBusy, ProbeLeaseManager
from jlink_mcp.models import (
    Artifact,
    DependencyCheck,
    DependencyReport,
    DeviceSelector,
    ValidationReport,
    ValidationStep,
)
from jlink_mcp.security import (
    UnsafeCommand,
    validate_application_args,
    validate_gdb_command,
    validate_raw_command,
    validate_raw_commands,
)
from jlink_mcp.service import JLinkService
from jlink_mcp.store import AuditStore, sha256_file


def test_selector_and_result_contracts(manifest) -> None:
    empty_selector = DeviceSelector(probe_serial=None, target_profile=None)
    assert empty_selector.probe_serial is None
    assert empty_selector.target_profile is None
    selector = DeviceSelector(
        probe_serial=" 00-AB_12 ",
        target_profile="sample_target",
        core="secondary",
    )
    assert selector.probe_serial == "00-AB_12"
    assert selector.core == "secondary"
    for invalid in ("", "!bad", "has space"):
        with pytest.raises(ValidationError):
            DeviceSelector(probe_serial=invalid)
        with pytest.raises(ValidationError):
            DeviceSelector(target_profile=invalid)
    with pytest.raises(ValidationError):
        DeviceSelector(speed_khz=1)
    result = make_result()
    assert result.ok
    assert result.model_dump()["ok"] is True
    assert not make_result(return_code=1).ok
    assert not make_result(timed_out=True).ok
    report = DependencyReport(
        checks=[
            DependencyCheck(name="required", ok=True),
            DependencyCheck(name="optional", ok=False, required=False),
        ],
        manifest=manifest,
    )
    assert report.ok
    report.checks[0].ok = False
    assert not report.ok
    now = datetime.now(UTC)
    validation = ValidationReport(
        run_id="r",
        started_at=now,
        finished_at=now,
        selector=selector,
        steps=[ValidationStep(name="one", ok=True)],
    )
    assert validation.ok
    validation.steps.append(ValidationStep(name="two", ok=False))
    assert not validation.ok


def test_extension_allowlist_accepts_compose_environment_syntax(monkeypatch) -> None:
    monkeypatch.setenv("JLINK_MCP_EXTENSIONS", "")
    assert Settings(_env_file=None).extensions == []
    monkeypatch.setenv("JLINK_MCP_EXTENSIONS", "arduino_giga,giga_protocol_bridge")
    assert Settings(_env_file=None).extensions == [
        "arduino_giga",
        "giga_protocol_bridge",
    ]


def test_udev_policy_precedes_vendor_final_assignment() -> None:
    repository = Path(__file__).resolve().parents[1]
    rules = (repository / "config/59-jlink-mcp.rules").read_text()
    installer = (repository / "scripts/install-udev-rules.sh").read_text()
    assert 'ATTR{idVendor}=="1366", MODE:="0660", GROUP:="plugdev"' in rules
    assert 'ATTR{idVendor}=="2341"' not in rules
    assert 'KERNEL=="ttyACM*"' not in rules
    assert "destination=/etc/udev/rules.d/59-jlink-mcp.rules" in installer
    assert "sudo -v" in installer
    assert 'if [ "$failed" -ne 0 ]' in installer


def test_repository_license_is_copyable_but_noncommercial() -> None:
    repository = Path(__file__).resolve().parents[1]
    license_text = (repository / "LICENSE").read_text()
    metadata = (repository / "pyproject.toml").read_text()
    readme = (repository / "README.md").read_text()
    assert license_text.startswith("# PolyForm Noncommercial License 1.0.0")
    assert "## Distribution License" in license_text
    assert "## Changes and New Works License" in license_text
    assert "Any noncommercial purpose is a permitted purpose." in license_text
    assert 'license = "PolyForm-Noncommercial-1.0.0"' in metadata
    assert "revenue-generating product, service, workflow, or business model" in readme
    assert "source-available, not OSI open source" in readme


def test_artifact_and_profiles(tmp_path: Path, target_registry) -> None:
    path = tmp_path / "firmware.bin"
    path.write_bytes(b"abc")
    digest = hashlib.sha256(b"abc").hexdigest()
    artifact = Artifact.from_path(path, kind="bin", sha256=digest, metadata={"x": 1})
    assert artifact.size == 3
    assert artifact.metadata == {"x": 1}
    assert sha256_file(path, block_size=1) == digest
    profile = target_registry.get_profile("sample_target")
    assert profile.display_name == "Sample target"
    assert target_registry.jlink_device(profile.id, "primary") == "SAMPLE_PRIMARY"
    assert target_registry.jlink_device(profile.id, "secondary") == "SAMPLE_SECONDARY"
    with pytest.raises(ValueError, match="unknown target profile"):
        target_registry.get_profile("unknown")


def test_settings_token_and_path_confinement(
    settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    assert settings.bearer_token() == "test-token"
    settings.token = " inline "
    assert settings.bearer_token() == "inline"
    settings.token = None
    settings.token_file = None
    monkeypatch.setenv("JLINK_MCP_TOKEN", " env-token ")
    assert settings.bearer_token() == "env-token"
    monkeypatch.delenv("JLINK_MCP_TOKEN")
    assert settings.bearer_token(required=False) is None
    with pytest.raises(RuntimeError, match="bearer token"):
        settings.bearer_token()

    inside = settings.workspace_root / "a.bin"
    inside.write_bytes(b"x")
    assert settings.resolve_workspace_path("a.bin") == inside
    assert settings.resolve_allowed_path(inside) == inside
    future = settings.resolve_allowed_path("new.bin", must_exist=False)
    assert future.parent == settings.workspace_root
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x")
    with pytest.raises(ValueError, match="outside"):
        settings.resolve_workspace_path(outside)
    with pytest.raises(ValueError, match="outside"):
        settings.resolve_allowed_path(outside)

    first = tmp_path / "first"
    second = tmp_path / "second"
    second.touch()
    assert _first_existing(str(first), str(second)) == second.resolve()
    second.unlink()
    assert _first_existing(str(first), str(second)) == first


@pytest.mark.parametrize(
    "command",
    ["H", "Mem32 0x20000000 1", "LoadFile ./firmware.bin", "ShowHWStatus"],
)
def test_valid_raw_commands(command: str) -> None:
    assert validate_raw_command(command) == command


@pytest.mark.parametrize(
    "command",
    ["", "H; whoami", "H | id", "$(id)", "`id`", "bad/name x", "H\nExit"],
)
def test_raw_shell_escape_rejected(command: str) -> None:
    with pytest.raises(UnsafeCommand):
        validate_raw_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "-exec-continue",
        "-data-evaluate-expression &jlink_mcp_watch_value",
        "-data-read-memory-bytes $sp 128",
        "monitor reset",
        '-interpreter-exec console "monitor reset"',
        "x/16wx $sp",
        "set $r0=1",
    ],
)
def test_valid_gdb_commands(command: str) -> None:
    assert validate_gdb_command(command) == command


@pytest.mark.parametrize(
    "command",
    [
        "shell id",
        "!id",
        "python print(1)",
        "source /tmp/x",
        "set environment X Y",
        "set sysroot /tmp",
        "-file-exec-and-symbols /tmp/x",
        '-interpreter-exec console "shell id"',
        "-unknown-command",
        "maintenance info",
        "x\nquit",
    ],
)
def test_gdb_host_escape_rejected(command: str) -> None:
    with pytest.raises(UnsafeCommand):
        validate_gdb_command(command)


def test_path_operands_confined(settings: Settings, tmp_path: Path) -> None:
    firmware = settings.workspace_root / "firmware.bin"
    firmware.write_bytes(b"x")
    assert validate_raw_commands([f'LoadFile "{firmware}"'], settings=settings)
    assert validate_application_args(["-open", str(firmware)], settings) == [
        "-open",
        str(firmware),
    ]
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x")
    with pytest.raises(UnsafeCommand):
        validate_raw_commands([f"LoadFile {outside}"], settings=settings)
    with pytest.raises(ValueError):
        validate_application_args([f"--file={outside}"], settings)
    with pytest.raises(UnsafeCommand):
        validate_raw_commands([], settings=settings)
    with pytest.raises(UnsafeCommand):
        validate_raw_commands(["H"] * 257, settings=settings)
    with pytest.raises(UnsafeCommand):
        validate_application_args(["ok"] * 257, settings)
    with pytest.raises(UnsafeCommand):
        validate_application_args(["bad;id"], settings)


@pytest.mark.asyncio
async def test_exclusive_leases_and_stale_release() -> None:
    manager = ProbeLeaseManager()
    first = await manager.acquire("probe", owner="first")
    assert manager.active_leases()[0].owner == "first"
    with pytest.raises(ProbeBusy, match="first"):
        await manager.acquire("probe", owner="second", timeout=0.01)
    other = await manager.acquire("other", owner="parallel")
    assert len(manager.active_leases()) == 2
    await manager.release("missing")
    await manager.release(first.lease_id)
    async with manager.lease("probe", owner="third", timeout=0.1) as lease:
        assert lease.owner == "third"
        nested_id = lease.lease_id
        async with manager.lease("probe", owner="nested", timeout=0.1) as nested:
            assert nested.lease_id == nested_id
            assert nested.owner == "third"
            assert len(manager.active_leases()) == 2
        assert len(manager.active_leases()) == 2
    await manager.release(other.lease_id)
    assert not manager.active_leases()


def test_audit_hash_chain_sessions_and_verified_target(tmp_path: Path) -> None:
    store = AuditStore(tmp_path / "state" / "audit.sqlite3")
    result = make_result(
        parsed={"connected": True},
    )
    result.probe_identity = {"serial": "probe"}
    result.target_identity = {
        "board_serial": "board",
        "cpuid": "0x411FC271",
        "dpidr": "0x6BA02477",
    }
    first_hash = store.append_operation(
        result=result,
        action="connect",
        probe_serial="probe",
        destructive=False,
        request={"x": 1},
    )
    assert len(first_hash) == 64
    assert store.verify_chain() == (True, None)
    assert store.has_verified_target("board", "probe")
    assert not store.has_verified_target("wrong", "probe")

    artifact_path = tmp_path / "a.bin"
    artifact_path.write_bytes(b"abc")
    artifact = Artifact.from_path(
        artifact_path,
        kind="bin",
        sha256=hashlib.sha256(b"abc").hexdigest(),
    )
    store.register_artifact(artifact)
    assert store.list_artifacts()[0]["sha256"] == artifact.sha256
    store.upsert_session(
        session_id="s", probe_serial="probe", backend="gdb", state={"port": 1}
    )
    stale = store.clear_stale_sessions()
    assert stale[0]["session_id"] == "s"
    assert store.clear_stale_sessions() == []
    store.upsert_session(session_id="s2", probe_serial="probe", backend="gdb", state={})
    store.delete_session("s2")
    assert store.list_operations(limit=1)[0]["action"] == "connect"

    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE operations SET payload_json = ? WHERE sequence = 1", ("{}",)
        )
    ok, error = store.verify_chain()
    assert not ok
    assert "entry hash mismatch" in str(error)


def test_service_startup_audits_stale_session_recovery(settings: Settings) -> None:
    store = AuditStore(settings.state_root / "jlink-mcp.sqlite3")
    store.upsert_session(
        session_id="stale",
        probe_serial="probe",
        backend="gdb",
        state={"pid": 999999},
    )
    service = JLinkService(settings)
    operation = service.store.list_operations(limit=1)[0]
    assert operation["action"] == "recover_stale_sessions"
    assert (
        operation["payload"]["result"]["parsed"]["recovered"][0]["session_id"]
        == "stale"
    )
