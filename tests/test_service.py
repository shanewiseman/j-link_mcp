from __future__ import annotations

import copy
import asyncio
import json
from pathlib import Path

import pytest

import jlink_mcp.service as service_module
from jlink_mcp.models import DependencyReport, DeviceSelector, TargetCore
from jlink_mcp.bridge_models import WifiConnectRequest
from jlink_mcp.service import JLinkService, TargetSelectionError

from conftest import make_result


PROBE = "000802008248"
BOARD = "0045002B3333511632363530"


def identity(core: TargetCore = TargetCore.M7):
    return make_result(
        parsed={
            "connected": True,
            "core": "Cortex-M7" if core == TargetCore.M7 else "Cortex-M4",
            "cpuid": "0x411FC271" if core == TargetCore.M7 else "0x410FC241",
            "dpidr": "0x6BA02477",
            "target_voltage": 3.284,
            "probe_serial": PROBE,
            "firmware": "J-Link EDU Mini V2",
            "hardware_version": "V2.00",
            "licenses": ["FlashBP", "GDB"],
        }
    )


@pytest.fixture
def service(settings, manifest, monkeypatch):
    instance = JLinkService(settings)
    monkeypatch.setattr(instance, "capabilities", lambda: copy.deepcopy(manifest))
    monkeypatch.setattr(instance, "_serial_port_ready", lambda path: True)

    async def resume(resolved, lease_id, identity_data, **kwargs):
        return make_result()

    monkeypatch.setattr(instance, "_resume_after_identity_preflight", resume)
    return instance


def selector(core=TargetCore.M7):
    return DeviceSelector(probe_serial=PROBE, board_serial=BOARD, core=core)


def test_capability_and_doctor_merge_sparse_recent_probe_evidence(
    settings, manifest, monkeypatch
) -> None:
    instance = JLinkService(settings)
    monkeypatch.setattr(
        service_module, "capability_manifest", lambda settings: copy.deepcopy(manifest)
    )
    monkeypatch.setattr(
        service_module,
        "dependency_report",
        lambda settings: DependencyReport(checks=[], manifest=copy.deepcopy(manifest)),
    )
    licensed = make_result()
    licensed.probe_identity = {
        "serial": PROBE,
        "observed_serial": PROBE.lstrip("0"),
        "firmware": "J-Link EDU Mini V2",
        "hardware_version": "V2.00",
        "licenses": ["FlashBP", "GDB"],
    }
    instance.store.append_operation(
        result=licensed, action="probe_info", probe_serial=PROBE, destructive=False
    )
    sparse = make_result()
    sparse.probe_identity = {"serial": PROBE}
    instance.store.append_operation(
        result=sparse, action="stop_gui", probe_serial=PROBE, destructive=False
    )

    probe = instance.capabilities().probes[0]
    assert probe.firmware == "J-Link EDU Mini V2"
    assert probe.licenses == ["FlashBP", "GDB"]
    license_check = next(
        check for check in instance.doctor().checks if check.name == "probe-licenses"
    )
    assert license_check.ok


def test_selector_unique_explicit_ambiguity_and_audit_reconnect(service, manifest, monkeypatch) -> None:
    resolved = service.resolve_selector(None)
    assert resolved.probe_serial == PROBE and resolved.board_serial == BOARD
    assert service.resolve_selector(selector()).core == TargetCore.M7
    with pytest.raises(TargetSelectionError, match="not attached"):
        service.resolve_selector(DeviceSelector(probe_serial="WRONG"))

    no_probes = copy.deepcopy(manifest)
    no_probes.probes = []
    monkeypatch.setattr(service, "capabilities", lambda: no_probes)
    with pytest.raises(TargetSelectionError, match="ambiguous"):
        service.resolve_selector(None)

    many = copy.deepcopy(manifest)
    many.probes.append(copy.deepcopy(many.probes[0]))
    many.probes[-1].serial = "SECOND"
    monkeypatch.setattr(service, "capabilities", lambda: many)
    with pytest.raises(TargetSelectionError, match="ambiguous"):
        service.resolve_selector(DeviceSelector(board_serial=BOARD))

    boards = copy.deepcopy(manifest)
    boards.boards.append(copy.deepcopy(boards.boards[0]))
    boards.boards[-1].serial = "SECOND_BOARD"
    monkeypatch.setattr(service, "capabilities", lambda: boards)
    with pytest.raises(TargetSelectionError, match="board selection is ambiguous"):
        service.resolve_selector(DeviceSelector(probe_serial=PROBE))


@pytest.mark.asyncio
async def test_connect_identity_gate_and_commander_audit(service, monkeypatch) -> None:
    calls = []

    async def execute(commands, **kwargs):
        calls.append(list(commands))
        core = kwargs.get("selector", selector()).core
        return identity(core)

    monkeypatch.setattr(service.commander, "execute", execute)
    connected = await service.connect(selector())
    assert connected.ok
    assert connected.probe_identity["serial"] == PROBE
    assert connected.target_identity["cpuid"] == "0x411FC271"
    halted = await service.halt(selector())
    assert halted.ok and halted.parsed["identity_preflight"]["connected"]
    assert len(calls) == 3
    assert service.store.verify_chain() == (True, None)
    assert service.store.has_verified_target(BOARD, PROBE)

    async def wrong(commands, **kwargs):
        bad = identity()
        bad.parsed["cpuid"] = "0xDEADBEEF"
        return bad

    monkeypatch.setattr(service.commander, "execute", wrong)
    with pytest.raises(TargetSelectionError, match="positive target identification"):
        await service.connect(selector())
    # Failed positive identification is still immutable evidence.
    assert service.store.list_operations(limit=1)[0]["action"] == "connect"

    monkeypatch.setattr(service.commander, "execute", execute)
    await service.raw(["Erase"], selector=selector(), destructive=False)
    irreversible = service.store.list_operations(limit=1)[0]
    assert irreversible["action"] == "raw_commander"
    assert irreversible["destructive"] is True


@pytest.mark.asyncio
async def test_hotplug_retry_and_timeout(service, monkeypatch) -> None:
    attempts = 0
    original = service.resolve_selector

    def intermittent(value):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise TargetSelectionError("renumbering")
        return original(value)

    async def no_sleep(value):
        return None

    monkeypatch.setattr(service, "resolve_selector", intermittent)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    assert (await service.resolve_selector_wait(selector(), timeout=1)).probe_serial == PROBE
    monkeypatch.setattr(
        service,
        "resolve_selector",
        lambda value: (_ for _ in ()).throw(TargetSelectionError("gone")),
    )
    with pytest.raises(TargetSelectionError, match="gone"):
        await service.resolve_selector_wait(selector(), timeout=0)


@pytest.mark.asyncio
async def test_serial_waits_for_post_identity_usb_renumeration(
    service, monkeypatch
) -> None:
    events: list[str] = []
    readiness = iter((False, False, True))

    async def preflight(resolved, lease_id):
        events.append("identity")
        result = identity(resolved.core)
        result.session_id = lease_id
        return result

    async def no_sleep(value):
        return None

    async def resume(resolved, lease_id, identity_data, **kwargs):
        events.append("resume")
        return make_result()

    async def exchange(port, **kwargs):
        events.append(f"exchange:{port}")
        return make_result(parsed={"port": port, "records": []})

    def ready(path):
        events.append(f"ready:{path}")
        return next(readiness)

    monkeypatch.setattr(service, "_identity_preflight", preflight)
    monkeypatch.setattr(service, "_resume_after_identity_preflight", resume)
    monkeypatch.setattr(service, "_serial_port_ready", ready)
    monkeypatch.setattr(service.serial, "exchange", exchange)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    result = await service.serial_exchange(
        selector=selector(), write="PING", duration=0.1
    )
    assert result.ok
    assert events == [
        "identity",
        "resume",
        "ready:/dev/ttyACM0",
        "ready:/dev/ttyACM0",
        "ready:/dev/ttyACM0",
        "exchange:/dev/ttyACM0",
    ]


@pytest.mark.asyncio
async def test_protocol_bridge_identity_handshake_and_secret_audit(
    service, monkeypatch, tmp_path
) -> None:
    events: list[str] = []

    async def preflight(resolved, lease_id):
        events.append(f"identity:{resolved.core.value}")
        result = identity(resolved.core)
        result.session_id = lease_id
        return result

    async def bridge_request(port, request, **kwargs):
        events.append(f"bridge:{port}:{kwargs.get('operation') or request.operation}")
        if kwargs.get("operation") == "get_status":
            return make_result(
                backend="giga-protocol-bridge",
                parsed={
                    "bridge": {
                        "status": 0,
                        "data_base64": "",
                        "metadata": {
                            "firmware_version": "1.0.0",
                            "wire_version": 1,
                            "build_id": "fixture",
                            "source_sha256": "a" * 64,
                            "supported_interfaces": ["spi", "i2c", "uart", "can", "usb", "wifi", "ble", "gpio"],
                            "safe_pins": ["D22"],
                            "transfer_limits": {"application_payload": 64000},
                            "connections": {},
                            "queue_depths": {},
                            "overflow_counts": {},
                            "active_resource_conflicts": [],
                        },
                    }
                },
            )
        assert kwargs["secrets_to_send"] == {
            "ssid": "fixture-net",
            "password": "secret123",
        }
        return make_result(
            backend="giga-protocol-bridge",
            parsed={
                "bridge": {
                    "status": 0,
                    "data_base64": "",
                    "metadata": {"connected": True},
                }
            },
        )

    profiles = tmp_path / "bridge-profiles.json"
    profiles.write_text(
        json.dumps(
            {"wifi": {"lab": {"ssid": "fixture-net", "password": "secret123"}}}
        ),
        encoding="utf-8",
    )
    profiles.chmod(0o600)
    service.settings.bridge_profiles_file = profiles
    monkeypatch.setattr(service, "_identity_preflight", preflight)
    monkeypatch.setattr(service.bridge, "request", bridge_request)

    status = await service.protocol_bridge_status(selector=selector())
    assert status.wire_version == 1 and status.firmware_version == "1.0.0"
    connected = await service.protocol_bridge_control(
        WifiConnectRequest(operation="wifi_connect", profile="lab"),
        selector=selector(),
    )
    assert connected.protocol == "wifi"
    assert events == [
        "identity:m7",
        "bridge:/dev/ttyACM0:get_status",
        "identity:m7",
        "bridge:/dev/ttyACM0:wifi_connect",
    ]
    audit = json.dumps(service.store.list_operations(limit=20), sort_keys=True)
    assert "fixture-net" not in audit and "secret123" not in audit
    assert '"profile": "lab"' in audit
    assert service.store.verify_chain() == (True, None)

    with pytest.raises(ValueError, match="runs on the GIGA M7"):
        await service.protocol_bridge_status(selector=selector(TargetCore.M4))


def test_identity_gate_all_failure_reasons(service) -> None:
    invalid = identity()
    invalid.return_code = 1
    invalid.parsed.update(
        connected=False,
        core="Cortex-M0",
        cpuid="bad",
        dpidr="bad",
        target_voltage=0.0,
        probe_serial="wrong",
    )
    with pytest.raises(TargetSelectionError) as error:
        service._validate_identity(invalid, selector())
    message = str(error.value)
    assert "did not connect" in message
    assert "CPUID" in message and "SW-DP" in message and "voltage" in message
    assert service._serial_equal("00012", "12")


@pytest.mark.asyncio
async def test_all_atomic_service_wrappers_and_validation(service, monkeypatch) -> None:
    calls = []

    async def commands(items, **kwargs):
        calls.append((list(items), kwargs))
        return make_result(parsed={"flash_verified": True})

    monkeypatch.setattr(service, "commander_commands", commands)
    bin_path = service.settings.workspace_root / "image.bin"
    bin_path.write_bytes(b"bin")

    assert (await service.reset(selector(), halt=True)).ok
    assert (await service.reset(selector(), halt=False)).ok
    assert (await service.reset(selector(), halt=False, reset_type=2)).ok
    assert calls[-1][0] == ["RSetType 2", "Reset", "Go"]
    with pytest.raises(ValueError, match="reset_type"):
        await service.reset(selector(), reset_type=16)
    assert (await service.halt(selector())).ok
    assert (await service.go(selector())).ok
    assert (await service.step(selector(), count=2)).ok
    assert (await service.read_memory(0x20000000, count=2, width=8, selector=selector())).ok
    assert (await service.write_memory(0x20000000, [1, 2], width=16, selector=selector())).ok
    assert (await service.set_breakpoint(0x08000000, selector=selector())).ok
    assert (await service.clear_breakpoint(2, selector=selector())).ok
    assert (await service.read_register("R0", selector=selector())).ok
    assert (await service.write_register("R0", 1, selector=selector())).ok
    assert (await service.set_watchpoint(0x20000000, "r", selector=selector())).ok
    assert (await service.clear_watchpoint(1, selector=selector())).ok
    assert (await service.erase_flash(selector=selector())).ok
    assert (await service.erase_flash(0x08000000, 0x08001000, selector=selector())).ok
    assert (await service.verify_binary(str(bin_path), 0x08000000, selector=selector())).ok
    assert (await service.probe_info(selector())).ok
    assert (await service.command_string("SetResetType 2", selector=selector())).ok
    for action in ("speeds", "status", "stop", "capture"):
        assert (await service.swo(action, speed_hz=1000000, capture_ms=1, selector=selector())).ok
    assert (await service.raw(["H"], selector=selector(), destructive=False)).ok
    assert len(calls) == 24

    invalid_calls = [
        lambda: service.step(selector(), count=0),
        lambda: service.read_memory(-1, selector=selector()),
        lambda: service.read_memory(0, width=64, selector=selector()),
        lambda: service.write_memory(0, [], selector=selector()),
        lambda: service.write_memory(0, [256], width=8, selector=selector()),
        lambda: service.write_memory(0, [1], width=64, selector=selector()),
        lambda: service.read_register("bad!", selector=selector()),
        lambda: service.write_register("R0", -1, selector=selector()),
        lambda: service.set_watchpoint(-1, selector=selector()),
        lambda: service.set_watchpoint(0, "X", selector=selector()),
        lambda: service.clear_breakpoint(256, selector=selector()),
        lambda: service.clear_watchpoint(-1, selector=selector()),
        lambda: service.erase_flash(0, None, selector=selector()),
        lambda: service.erase_flash(2, 1, selector=selector()),
        lambda: service.verify_binary(str(bin_path.with_suffix(".hex")), 0, selector=selector()),
        lambda: service.swo("capture", speed_hz=4000001, selector=selector()),
        lambda: service.swo("capture", capture_ms=0, selector=selector()),
        lambda: service.swo("invalid", selector=selector()),
    ]
    for call in invalid_calls:
        with pytest.raises((ValueError, FileNotFoundError)):
            await call()


@pytest.mark.asyncio
async def test_disconnect_probe_list_application_and_serial(service, monkeypatch) -> None:
    monkeypatch.setattr(service.commander, "probe_list", lambda: None)

    async def probe_list():
        return make_result()

    monkeypatch.setattr(service.commander, "probe_list", probe_list)
    assert (await service.probe_list()).ok
    assert (await service.disconnect(selector())).parsed["disconnected"]

    async def preflight(resolved, lease_id):
        result = identity(resolved.core)
        result.session_id = lease_id
        return result

    async def execute_app(application, args, **kwargs):
        return make_result(backend=application)

    monkeypatch.setattr(service, "_identity_preflight", preflight)
    monkeypatch.setattr(service.application, "execute", execute_app)
    assert (await service.run_application("JLinkExe", ["-?"], destructive=False)).ok
    with pytest.raises(ValueError, match="help/version"):
        await service.run_application("JLinkExe", [], destructive=False)
    app = await service.run_application(
        "JLinkRTTLoggerExe", [], destructive=True, selector=selector()
    )
    assert app.probe_identity["serial"] == PROBE
    with pytest.raises(ValueError, match="between 0 and 5"):
        await service.run_application(
            "JLinkRTTLoggerExe",
            [],
            destructive=True,
            selector=selector(),
            resume_settle_seconds=6,
        )
    with pytest.raises(ValueError, match="requires resume_after_preflight"):
        await service.run_application(
            "JLinkRTTLoggerExe",
            [],
            destructive=True,
            selector=selector(),
            resume_settle_seconds=1,
        )
    with pytest.raises(ValueError, match="between 1 and 3"):
        await service.run_application(
            "JLinkRTTLoggerExe", [], destructive=True, selector=selector(), attempts=0
        )
    with pytest.raises(ValueError, match="more than one attempt"):
        await service.run_application(
            "JLinkRTTLoggerExe",
            [],
            destructive=True,
            selector=selector(),
            retry_delay_seconds=1,
        )

    attempts = iter((make_result(return_code=255), make_result()))
    sleeps: list[float] = []

    async def execute_retry(application, args, **kwargs):
        return next(attempts)

    async def record_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(service.application, "execute", execute_retry)
    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    retried = await service.run_application(
        "JLinkRTTLoggerExe",
        [],
        destructive=True,
        selector=selector(),
        resume_after_preflight=True,
        attempts=2,
        retry_delay_seconds=0.25,
    )
    assert retried.ok
    assert sleeps == [0.25]
    assert [item["return_code"] for item in retried.parsed["application_attempts"]] == [
        255,
        0,
    ]

    async def exchange(port, **kwargs):
        return make_result(parsed={"port": port, "records": []})

    monkeypatch.setattr(service.serial, "exchange", exchange)
    serial = await service.serial_exchange(selector=selector(), write="PING", duration=0.1)
    assert serial.target_identity["board_serial"] == BOARD


@pytest.mark.asyncio
async def test_managed_gdb_lifecycle_and_channels(service, monkeypatch) -> None:
    async def preflight(resolved, lease_id):
        result = identity(resolved.core)
        result.session_id = lease_id
        return result

    monkeypatch.setattr(service, "_identity_preflight", preflight)
    monkeypatch.setattr(service.gdb, "start", lambda *args, **kwargs: None)

    async def start(resolved, elf_path=None):
        return "gdb-session"

    monkeypatch.setattr(service.gdb, "start", start)
    monkeypatch.setattr(
        service.gdb,
        "session_info",
        lambda session_id: {
            "session_id": session_id,
            "probe_serial": PROBE,
            "gdb_port": 1,
            "swo_port": 2,
            "telnet_port": 3,
            "rtt_port": 4,
            "created_at": "now",
            "elf_path": None,
        },
    )

    async def command(session_id, text, timeout=30):
        result = make_result(parsed={"mi": [], "session_id": session_id})
        result.session_id = session_id
        return result

    async def capture(session_id, channel, **kwargs):
        result = make_result(parsed={"channel": channel})
        result.session_id = session_id
        return result

    async def stop(session_id, resume=True):
        return None

    monkeypatch.setattr(service.gdb, "command", command)
    monkeypatch.setattr(service.gdb, "capture_port", capture)
    monkeypatch.setattr(service.gdb, "stop", stop)
    info = await service.start_gdb(selector=selector())
    assert info["session_id"] == "gdb-session"
    assert len(service.leases.active_leases()) == 1
    assert (await service.gdb_command("gdb-session", "-thread-info")).ok
    assert (await service.capture_gdb_channel("gdb-session", "rtt", write="x")).ok
    await service.stop_gdb("gdb-session", resume=True)
    assert not service.leases.active_leases()
    await service.stop_gdb("missing")


@pytest.mark.asyncio
async def test_managed_gui_lifecycle_ocr_and_audit(service, monkeypatch) -> None:
    async def preflight(resolved, lease_id):
        result = identity(resolved.core)
        result.session_id = lease_id
        return result

    async def launch(application, args):
        return "gui-session"

    async def result(*args, **kwargs):
        return make_result()

    async def stop(session_id):
        return None

    monkeypatch.setattr(service, "_identity_preflight", preflight)
    monkeypatch.setattr(service.gui, "launch", launch)
    monkeypatch.setattr(service.gui, "keys", result)
    monkeypatch.setattr(service.gui, "click", result)
    monkeypatch.setattr(service.gui, "screenshot", result)
    monkeypatch.setattr(service.gui, "accessibility_tree", result)
    monkeypatch.setattr(
        service.gui,
        "session_info",
        lambda session_id: {
            "session_id": session_id,
            "application": "JLinkConfigExe",
            "running": True,
        },
    )
    monkeypatch.setattr(service.gui, "image_match", result)
    monkeypatch.setattr(service.gui, "ocr", result)
    monkeypatch.setattr(service.gui, "stop", stop)
    info = await service.start_gui("JLinkConfigExe", [], selector=selector())
    assert info["session_id"] == "gui-session"
    assert (await service.gui_keys("gui-session", "a")).session_id == "gui-session"
    assert (await service.gui_click("gui-session", 1, 2)).ok
    assert (await service.gui_screenshot("gui-session")).ok
    assert (await service.gui_accessibility("gui-session")).ok
    assert service.gui_session_info("gui-session")["running"]
    template = service.settings.workspace_root / "template.png"
    template.write_bytes(b"png")
    assert (await service.gui_image_match("gui-session", template, threshold=0.8)).ok
    screenshot = service.settings.state_root / "screenshots" / "shot.png"
    screenshot.write_bytes(b"image")
    ocr = await service.gui_ocr(str(screenshot))
    assert ocr.artifact_hashes[str(screenshot)]
    outside = service.settings.repository_root.parent / "outside.png"
    outside.write_bytes(b"bad")
    with pytest.raises(ValueError):
        await service.gui_ocr(str(outside))
    await service.stop_gui("gui-session")
    assert not service.leases.active_leases()


@pytest.mark.asyncio
async def test_service_close_cleans_managed_sessions(service, monkeypatch) -> None:
    called = []

    async def stop_gdb(session_id, resume=True):
        called.append(("gdb", session_id))
        service._gdb_leases.pop(session_id, None)

    async def stop_gui(session_id):
        called.append(("gui", session_id))
        service._gui_leases.pop(session_id, None)

    async def stop_all():
        called.append(("all", None))

    service._gdb_leases["g"] = "lease"
    service._gui_leases["u"] = "lease"
    monkeypatch.setattr(service, "stop_gdb", stop_gdb)
    monkeypatch.setattr(service, "stop_gui", stop_gui)
    monkeypatch.setattr(service.gui, "stop_all", stop_all)
    await service.close()
    assert called == [("gdb", "g"), ("gui", "u"), ("all", None)]
