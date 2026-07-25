from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from jlink_mcp.models import (
    Artifact,
    DependencyCheck,
    DependencyReport,
    DeviceSelector,
    ValidationReport,
)
from jlink_mcp.extensions.api import ExtensionRegistry
from jlink_mcp.service import JLinkService
from jlink_mcp_arduino_giga.profiles import GIGA_R1, TargetCore
from jlink_mcp_arduino_giga.models import BuildResult
from jlink_mcp_arduino_giga.workflows import ArduinoGigaWorkflows
import jlink_mcp_arduino_giga.workflows as workflow_module

from .conftest import BOARD, PROBE, make_result, selector


@pytest.fixture
def workflow(settings, giga_config, manifest, monkeypatch):
    registry = ExtensionRegistry()
    registry.targets.register_profile(GIGA_R1)
    service = JLinkService(settings, registry)
    monkeypatch.setattr(service, "capabilities", lambda: copy.deepcopy(manifest))
    return ArduinoGigaWorkflows(service, giga_config)


def test_giga_bundle_resolves_omitted_profile_and_core(workflow) -> None:
    resolved = workflow.service.resolve_selector(
        DeviceSelector(probe_serial=PROBE, board_serial=BOARD)
    )
    assert resolved.target_profile == "arduino_giga_r1"
    assert resolved.core == "m7"


@pytest.mark.asyncio
async def test_build_identity_git_and_source_tree(workflow, monkeypatch) -> None:
    async def git_ok(*args, **kwargs):
        return make_result(stdout="a" * 40 + "\n")

    monkeypatch.setattr(workflow.service.runner, "run", git_ok)
    result = await workflow._build_identity()
    assert result["identity_kind"] == "git-commit"
    assert result["git_commit"] == "a" * 40
    assert result["build_timestamp"].endswith("Z")

    async def git_bad(*args, **kwargs):
        return make_result(return_code=1)

    monkeypatch.setattr(workflow.service.runner, "run", git_bad)
    (workflow.settings.repository_root / "tracked.txt").write_text("one")
    (workflow.settings.repository_root / ".token").write_text("secret")
    first = await workflow._build_identity()
    (workflow.settings.repository_root / ".token").write_text("changed")
    second = await workflow._build_identity()
    assert first["identity_kind"] == "source-tree-sha1"
    assert first["git_commit"] == second["git_commit"]
    (workflow.settings.repository_root / "tracked.txt").write_text("two")
    assert (await workflow._build_identity())["git_commit"] != first["git_commit"]


@pytest.mark.asyncio
async def test_build_firmware_success_and_failure_artifacts(workflow, monkeypatch) -> None:
    sketch = workflow.settings.workspace_root / "m7"
    sketch.mkdir()
    (sketch / "m7.ino").write_text("void setup(){} void loop(){}")

    async def build_identity():
        return {
            "git_commit": "a" * 40,
            "identity_kind": "git-commit",
            "build_id": "build",
            "build_timestamp": "2026-01-01T00:00:00Z",
        }

    async def properties(*args, **kwargs):
        return {"build.compiler_path": "/toolchain", "build.flash_start": "0x08000000"}

    async def run(argv, **kwargs):
        if "--output-dir" in argv:
            build_dir = Path(argv[argv.index("--output-dir") + 1])
            (build_dir / "fixture.elf").write_bytes(b"ELF")
        return make_result(stdout="ok")

    async def regenerate(elf, *, build_dir, properties):
        (build_dir / "fixture.bin").write_bytes(b"BIN")
        (build_dir / "fixture.hex").write_text(":00000001FF\n")

    async def analysis(elf, *, build_dir, properties):
        (build_dir / "fixture.symbols").write_text("symbols")
        (build_dir / "fixture.disassembly").write_text("disassembly")

    monkeypatch.setattr(workflow, "_build_identity", build_identity)
    monkeypatch.setattr(workflow, "_build_properties", properties)
    monkeypatch.setattr(workflow.service.runner, "run", run)
    monkeypatch.setattr(workflow, "_regenerate_flash_artifacts", regenerate)
    monkeypatch.setattr(workflow, "_generate_analysis_artifacts", analysis)
    monkeypatch.setattr(
        workflow_module,
        "finalize_fixture_elf",
        lambda path: {"flash_start": "0x08000000", "image_size": 3},
    )
    monkeypatch.setattr(workflow_module, "verify_fixture_elf", lambda path: {"ok": True})
    monkeypatch.setattr(
        workflow_module,
        "inspect_elf",
        lambda path: {"entry": 0x08000000, "test_symbols": {}},
    )
    build = await workflow.build_firmware("m7", core=TargetCore.M7)
    kinds = {artifact.kind for artifact in build.artifacts}
    assert {"elf", "bin", "hex", "symbols", "disassembly", "manifest", "checksums"} <= kinds
    header = next(Path(build.build_directory).glob("source/m7/JLinkMCPBuildIdentity.h"))
    assert "a" * 40 in header.read_text()
    manifest = ArduinoGigaWorkflows._build_manifest(build)
    assert manifest["embedded_manifest"]["verification"]["ok"]
    assert ArduinoGigaWorkflows._build_artifact(build, "bin").size == 3
    with pytest.raises(RuntimeError, match="no map"):
        ArduinoGigaWorkflows._build_artifact(build, "map")
    with pytest.raises(ValueError):
        await workflow.build_firmware("m7", core=TargetCore.M7, flash_split="bad")

    async def failed(argv, **kwargs):
        return make_result(return_code=1, stderr="compile failed")

    monkeypatch.setattr(workflow.service.runner, "run", failed)
    failed_build = await workflow.build_firmware("m7", core=TargetCore.M7)
    assert not failed_build.command.ok
    assert {item.kind for item in failed_build.artifacts} == {"manifest", "checksums"}


@pytest.mark.asyncio
async def test_build_properties_and_generated_artifacts(workflow, monkeypatch) -> None:
    sketch = workflow.settings.workspace_root / "sketch"
    sketch.mkdir()
    compiler = workflow.settings.workspace_root / "compiler"
    compiler.mkdir()
    elf = workflow.settings.workspace_root / "fixture.elf"
    elf.write_bytes(b"elf")
    build_dir = workflow.settings.state_root / "artifacts" / "gen"
    build_dir.mkdir()
    calls = []

    async def run(argv, **kwargs):
        calls.append([str(x) for x in argv])
        if "--show-properties" in argv:
            return make_result(stdout=f"build.compiler_path={compiler}\ninvalid line\nx=y=z\n")
        if "arm-none-eabi-objcopy" in str(argv[0]):
            Path(argv[-1]).write_bytes(b"generated")
            return make_result()
        return make_result(stdout="analysis output")

    monkeypatch.setattr(workflow.service.runner, "run", run)
    properties = await workflow._build_properties(sketch, core=TargetCore.M4, flash_split="75_25")
    assert properties["x"] == "y=z"
    await workflow._regenerate_flash_artifacts(
        elf, build_dir=build_dir, properties={"build.compiler_path": str(compiler)}
    )
    await workflow._generate_analysis_artifacts(
        elf, build_dir=build_dir, properties={"build.compiler_path": str(compiler)}
    )
    assert (build_dir / "fixture.bin").read_bytes() == b"generated"
    assert (build_dir / "fixture.symbols").read_text() == "analysis output"

    async def fail(*args, **kwargs):
        return make_result(return_code=1, stderr="tool failed")

    monkeypatch.setattr(workflow.service.runner, "run", fail)
    with pytest.raises(RuntimeError, match="objcopy failed"):
        await workflow._regenerate_flash_artifacts(
            elf, build_dir=build_dir, properties={"build.compiler_path": str(compiler)}
        )
    with pytest.raises(RuntimeError, match="failed"):
        await workflow._generate_analysis_artifacts(
            elf, build_dir=build_dir, properties={"build.compiler_path": str(compiler)}
        )


@pytest.mark.asyncio
async def test_flash_backup_compare_and_restore(workflow, monkeypatch) -> None:
    elf = workflow.settings.workspace_root / "image.elf"
    hex_path = workflow.settings.workspace_root / "image.hex"
    binary = workflow.settings.workspace_root / "image.bin"
    text = workflow.settings.workspace_root / "image.txt"
    for path, data in ((elf, b"elf"), (hex_path, b"hex"), (binary, b"bin"), (text, b"x")):
        path.write_bytes(data)
    calls = []

    async def commander(commands, **kwargs):
        calls.append((commands, kwargs))
        if commands[0].startswith("SaveBin"):
            destination = Path(commands[0].split('"')[1])
            destination.write_bytes(b"backup")
        return make_result(parsed={"flash_verified": True})

    monkeypatch.setattr(workflow.service, "commander_commands", commander)
    for path in (elf, hex_path):
        flashed = await workflow.flash_and_verify(str(path), selector=selector())
        assert flashed.artifact_hashes[str(path)]
    with pytest.raises(ValueError, match="explicit"):
        await workflow.flash_and_verify(str(binary))
    with pytest.raises(ValueError, match="ELF"):
        await workflow.flash_and_verify(str(text))
    flashed_bin = await workflow.flash_binary(str(binary), 0x08000000, selector=selector())
    assert flashed_bin.ok
    with pytest.raises(ValueError):
        await workflow.flash_binary(str(elf), 0)
    backup_result, backup = await workflow.backup_flash(0x08000000, 64, selector=selector())
    assert backup_result.ok and backup and backup.size == 6
    with pytest.raises(ValueError):
        await workflow.backup_flash(-1, 1)
    compared = await workflow.compare_firmware(str(binary), 0x08000000, selector=selector())
    assert compared["match"]
    region = await workflow.compare_backup_region(
        str(binary), 0, 0x08000000, 2, selector=selector()
    )
    assert region["match"] and region["region_artifact"]["size"] == 2
    with pytest.raises(ValueError):
        await workflow.compare_backup_region(str(binary), 2, 0, 2)

    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    restored = await workflow.restore_backup(str(binary), 0x08000000, digest, selector=selector())
    assert restored["ok"]
    with pytest.raises(ValueError, match="64-character"):
        await workflow.restore_backup(str(binary), 0, "bad")
    with pytest.raises(ValueError, match="does not match"):
        await workflow.restore_backup(str(binary), 0, "0" * 64)
    with pytest.raises(ValueError, match="raw BIN"):
        await workflow.restore_backup(str(elf), 0, hashlib.sha256(elf.read_bytes()).hexdigest())


def _doctor(manifest, ok=True):
    return DependencyReport(
        checks=[DependencyCheck(name="all", ok=ok)],
        manifest=manifest,
    )


@pytest.mark.asyncio
async def test_preflight_deploy_and_boot_observation(workflow, manifest, monkeypatch) -> None:
    async def resolve(value):
        return selector()

    async def result(*args, **kwargs):
        return make_result()

    monkeypatch.setattr(workflow.service, "resolve_selector_wait", resolve)
    monkeypatch.setattr(workflow.service, "connect", result)
    monkeypatch.setattr(workflow.service, "raw", result)
    monkeypatch.setattr(workflow.service, "doctor", lambda: _doctor(manifest))
    preflight = await workflow.hardware_preflight(selector=selector())
    assert preflight["ok"]

    async def rcc_snapshot(*args, **kwargs):
        return make_result(
            parsed={"memory": [{"address": "0x580244A0", "values": ["0x0"]}]}
        )

    writes: list[tuple[int, list[int], int]] = []

    async def rcc_write(address, values, *, width, selector):
        writes.append((address, values, width))
        return make_result()

    monkeypatch.setattr(workflow.service, "read_memory", rcc_snapshot)
    monkeypatch.setattr(workflow.service, "write_memory", rcc_write)
    prepared = await workflow.prepare_giga_dual_core_debug(selector=selector())
    assert prepared["ok"] and prepared["changed"]
    assert writes == [(0x580244A0, [0x8], 32)]
    combined = await workflow.hardware_preflight(
        selector=selector(), prepare_dual_core=True
    )
    assert combined["ok"] and combined["preparation"]["ok"]

    def fake_build(core):
        directory = workflow.settings.state_root / core.value
        directory.mkdir(exist_ok=True)
        binary = directory / f"{core.value}.bin"
        binary.write_bytes(core.value.encode())
        manifest_path = directory / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "embedded_manifest": {
                        "flash_start": "0x08180000"
                        if core == TargetCore.M4
                        else "0x08040000"
                    },
                    "identity": {"git_commit": "a" * 40},
                }
            )
        )
        return BuildResult(
            core=core,
            fqbn="arduino:mbed_giga:giga",
            build_directory=str(directory),
            command=make_result(),
            artifacts=[
                Artifact.from_path(binary, kind="bin", sha256=hashlib.sha256(binary.read_bytes()).hexdigest()),
                Artifact.from_path(manifest_path, kind="manifest", sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest()),
            ],
        )

    async def build(sketch, *, core, **kwargs):
        return fake_build(core)

    async def flash(*args, **kwargs):
        return make_result()

    monkeypatch.setattr(workflow, "build_firmware", build)
    monkeypatch.setattr(workflow, "flash_binary", flash)
    deployed = await workflow.dual_core_deploy(selector=selector())
    assert deployed["ok"]

    serial_durations = []

    async def serial(*args, write=None, duration=None, **kwargs):
        serial_durations.append(duration)
        records = {
            "PING": [{"event": "pong"}],
            "SELFTEST": [{"ok": True}],
            "RPC": [{"m4_heartbeat": 3}],
        }.get(write, [{"ok": True}])
        return make_result(parsed={"records": records})

    monkeypatch.setattr(workflow.service, "reset", result)
    monkeypatch.setattr(workflow.service, "serial_exchange", serial)
    observed = await workflow.boot_and_observe(selector=selector())
    assert observed["ok"]
    assert serial_durations == [3.0] * 5

    m7_elf = workflow.settings.workspace_root / "m7-heartbeat.elf"
    m4_elf = workflow.settings.workspace_root / "m4-heartbeat.elf"
    m7_elf.write_bytes(b"m7")
    m4_elf.write_bytes(b"m4")
    monkeypatch.setattr(
        "jlink_mcp_arduino_giga.workflows.inspect_elf",
        lambda path: {
            "test_symbols": {
                "jlink_mcp_heartbeat": {"address": 0x20000000}
            }
        },
    )
    counters = {TargetCore.M7: 0, TargetCore.M4: 10}

    async def read_memory(address, *, selector, **kwargs):
        counters[selector.core] += 1
        return make_result(
            parsed={
                "memory": [
                    {
                        "address": f"0x{address:08X}",
                        "values": [f"0x{counters[selector.core]:08X}"],
                    }
                ]
            }
        )

    monkeypatch.setattr(workflow.service, "read_memory", read_memory)
    observed = await workflow.boot_and_observe(
        selector=selector(),
        m7_elf_path=str(m7_elf),
        m4_elf_path=str(m4_elf),
    )
    assert observed["ok"]
    assert observed["heartbeat_progress"] == {"m7": True, "m4": True}


@pytest.mark.asyncio
async def test_debug_and_crash_workflows(workflow, monkeypatch) -> None:
    async def resolve(value):
        return selector()

    async def start_gdb(**kwargs):
        return {"session_id": "gdb"}

    thread_count = 0

    async def gdb(session_id, command, **kwargs):
        nonlocal thread_count
        parsed = {"mi": [{"type": "result", "message": "done", "payload": {}}]}
        if command == "-thread-info":
            reason = "breakpoint-hit" if thread_count == 0 else "watchpoint-trigger"
            thread_count += 1
            parsed = {"mi": [{"type": "notify", "message": "stopped", "payload": {"reason": reason}}]}
        elif "jlink_mcp_watch_value" in command and "evaluate" in command:
            parsed = {"mi": [{"message": "done", "payload": {"value": "2779077210"}}]}
        elif "read-memory-bytes" in command and command.endswith("16"):
            parsed = {"mi": [{"message": "done", "payload": {"memory": [{"contents": "00112233445566778899aabbccddeeff"}]}}]}
        elif command == "-stack-list-frames":
            parsed = {
                "mi": [
                    {
                        "message": "done",
                        "payload": {"stack": [{"func": "HardFault_Handler"}]},
                    }
                ]
            }
        result = make_result(parsed=parsed)
        result.command = [command]
        return result

    async def serial(*args, **kwargs):
        return make_result(parsed={"records": [{"ok": True}]})

    async def stop(*args, **kwargs):
        return None

    async def no_sleep(*args):
        return None

    monkeypatch.setattr(workflow.service, "resolve_selector_wait", resolve)
    monkeypatch.setattr(workflow.service, "start_gdb", start_gdb)
    monkeypatch.setattr(workflow.service, "gdb_command", gdb)
    monkeypatch.setattr(workflow.service, "serial_exchange", serial)
    monkeypatch.setattr(workflow.service, "stop_gdb", stop)
    monkeypatch.setattr(workflow_module.asyncio, "sleep", no_sleep)
    debug = await workflow.debug_fixture("fixture.elf", selector=selector())
    assert debug["ok"]

    # Crash capture uses the same fake session and verifies recovery in finally.
    thread_count = 0
    crash = await workflow.crash_capture("fixture.elf", selector=selector())
    assert crash["ok"]
    commands = [item["command"][0] for item in crash["commands"]]
    assert "-data-evaluate-expression $xpsr" in commands
    assert '-interpreter-exec console "monitor exec SetResetType=2"' in commands
    assert '-interpreter-exec console "monitor reset"' in commands
    assert '-interpreter-exec console "monitor go"' in commands


@pytest.mark.asyncio
async def test_rtt_capture_validation_and_reports(workflow, manifest, monkeypatch) -> None:
    elf = workflow.settings.workspace_root / "fixture.elf"
    elf.write_bytes(b"elf")

    async def resolve(value):
        return selector()

    calls: list[str] = []

    async def start_gdb(*, selector, elf_path):
        calls.append(f"start:{elf_path}")
        return {"session_id": "rtt-session"}

    async def gdb_command(session_id, command, **kwargs):
        assert session_id == "rtt-session"
        calls.append(command)
        return make_result()

    async def capture_channel(session_id, channel, **kwargs):
        assert session_id == "rtt-session" and channel == "rtt"
        calls.append(f"capture:{kwargs['duration']}")
        return make_result(stdout="RTT hello\n", parsed={"bytes": 10})

    async def stop_gdb(session_id, **kwargs):
        assert session_id == "rtt-session" and kwargs["resume"] is True
        calls.append("stop")

    async def no_sleep(seconds):
        calls.append(f"sleep:{seconds}")

    async def run_app(application, args, **kwargs):
        assert args[args.index("-RTTChannel") + 1] == "1"
        assert kwargs["resume_after_preflight"] is True
        assert kwargs["resume_settle_seconds"] == 4.0
        assert kwargs["attempts"] == 2
        assert kwargs["retry_delay_seconds"] == 4.0
        assert args[args.index("-RTTAddress") + 1] == "24000004"
        Path(args[-1]).write_text("RTT hello\n")
        return make_result(timed_out=True, return_code=-15)

    monkeypatch.setattr(workflow.service, "resolve_selector_wait", resolve)
    monkeypatch.setattr(workflow.service, "start_gdb", start_gdb)
    monkeypatch.setattr(workflow.service, "gdb_command", gdb_command)
    monkeypatch.setattr(workflow.service, "capture_gdb_channel", capture_channel)
    monkeypatch.setattr(workflow.service, "stop_gdb", stop_gdb)
    monkeypatch.setattr(workflow.service, "run_application", run_app)
    monkeypatch.setattr(workflow_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        workflow_module,
        "inspect_elf",
        lambda path: {"test_symbols": {"_SEGGER_RTT": {"address": 0x24000004}}},
    )
    captured = await workflow.capture_rtt(str(elf), duration_seconds=0.2)
    assert captured["ok"] and not captured["expected_timeout"]
    assert captured["backend"] == "gdb-rtt"
    assert "RTT hello" in captured["text"]
    assert '-interpreter-exec console "monitor exec SetRTTAddr=0x24000004"' in calls
    assert calls[-1] == "stop"
    assert workflow.service.store.list_operations(limit=1)[0]["action"] == "capture_rtt_evidence"
    logger = await workflow.capture_rtt(str(elf), duration_seconds=0.2, channel=1)
    assert logger["ok"] and logger["backend"] == "rtt-logger"
    with pytest.raises(ValueError):
        await workflow.capture_rtt(str(elf), duration_seconds=0.1)
    with pytest.raises(ValueError):
        await workflow.capture_rtt(str(elf), channel=16)
    monkeypatch.setattr(workflow_module, "inspect_elf", lambda path: {"test_symbols": {}})
    with pytest.raises(ValueError, match="_SEGGER_RTT"):
        await workflow.capture_rtt(str(elf))

    monkeypatch.setattr(workflow.service, "capabilities", lambda: manifest)
    monkeypatch.setattr(workflow.service, "doctor", lambda: _doctor(manifest))
    report = await workflow.generate_validation_report(title="Unit validation", audit_limit=100)
    assert report["audit_chain_ok"]
    paths = [Path(item["path"]) for item in report["artifacts"]]
    assert {path.suffix for path in paths} == {".json", ".md"}
    payload = json.loads(next(path for path in paths if path.suffix == ".json").read_text())
    assert payload["title"] == "Unit validation"
    markdown = next(path for path in paths if path.suffix == ".md").read_text()
    assert "## Hardware evidence" in markdown
    assert "## Dependency checks" in markdown
    assert "## Operation and command summary" in markdown
    assert "## Artifact hashes" in markdown
    assert "## Evidence paths" in markdown


@pytest.mark.asyncio
async def test_validate_fixture_preflight_stop_and_guaranteed_restore(
    workflow, monkeypatch
) -> None:
    async def resolve(value):
        return selector()

    monkeypatch.setattr(workflow.service, "resolve_selector_wait", resolve)
    monkeypatch.setattr(workflow, "hardware_preflight", lambda **kwargs: None)

    async def failed_preflight(**kwargs):
        return {"ok": False, "reason": "identity"}

    monkeypatch.setattr(workflow, "hardware_preflight", failed_preflight)
    stopped = await workflow.validate_fixture(selector=selector())
    assert not stopped.ok
    assert stopped.steps[0].name == "hardware_preflight"
    assert stopped.warnings

    async def preflight(**kwargs):
        return {"ok": True}

    backup_path = workflow.settings.state_root / "artifacts" / "original.bin"
    backup_path.write_bytes(b"original")
    backup = Artifact.from_path(
        backup_path,
        kind="flash-backup",
        sha256=hashlib.sha256(backup_path.read_bytes()).hexdigest(),
    )

    async def backup_flash(*args, **kwargs):
        return make_result(), backup

    def build_payload(core: str):
        root = workflow.settings.state_root / f"{core}-build"
        root.mkdir(exist_ok=True)
        elf = root / f"{core}-fixture.elf"
        elf.write_bytes(b"elf")
        artifact = Artifact.from_path(
            elf, kind="elf", sha256=hashlib.sha256(elf.read_bytes()).hexdigest()
        )
        return {
            "core": core,
            "fqbn": "arduino:mbed_giga:giga",
            "build_directory": str(root),
            "command": make_result().model_dump(mode="json"),
            "artifacts": [artifact.model_dump(mode="json")],
            "properties": {},
        }

    async def deploy(**kwargs):
        return {
            "ok": True,
            "m4_build": build_payload("m4"),
            "m7_build": build_payload("m7"),
        }

    async def ok_step(*args, **kwargs):
        return {"ok": True}

    async def restore(*args, **kwargs):
        return {"ok": True, "verify": "exact"}

    report_json = workflow.settings.state_root / "report.json"
    report_json.write_text("{}")
    report_artifact = Artifact.from_path(
        report_json,
        kind="validation-report-json",
        sha256=hashlib.sha256(report_json.read_bytes()).hexdigest(),
    )

    async def report(**kwargs):
        return {
            "audit_chain_ok": True,
            "artifacts": [report_artifact.model_dump(mode="json")],
        }

    monkeypatch.setattr(workflow, "hardware_preflight", preflight)
    monkeypatch.setattr(workflow, "backup_flash", backup_flash)
    monkeypatch.setattr(workflow, "dual_core_deploy", deploy)
    monkeypatch.setattr(workflow, "boot_and_observe", ok_step)
    monkeypatch.setattr(workflow, "debug_fixture", ok_step)
    monkeypatch.setattr(workflow, "crash_capture", ok_step)
    monkeypatch.setattr(workflow, "restore_backup", restore)
    monkeypatch.setattr(workflow, "generate_validation_report", report)
    validated = await workflow.validate_fixture(selector=selector())
    assert validated.ok
    assert validated.restored_original
    assert [step.name for step in validated.steps] == [
        "hardware_preflight",
        "backup_original_flash",
        "dual_core_deploy",
        "boot_and_observe",
        "debug_assertions",
        "controlled_crash",
        "restore_original_flash",
        "validation_report",
    ]

    async def explode(**kwargs):
        raise RuntimeError("injected deployment failure")

    monkeypatch.setattr(workflow, "dual_core_deploy", explode)
    failed = await workflow.validate_fixture(selector=selector())
    assert not failed.ok
    assert failed.restored_original
    assert any(step.name == "validation_exception" for step in failed.steps)
