from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import psutil
from pygdbmi.IoManager import GdbTimeoutError

from jlink_mcp.backends.application import ApplicationBackend
from jlink_mcp.backends.commander import (
    CommanderBackend,
    _infer_states,
    parse_commander_output,
)
from jlink_mcp.backends.gdb import GDBBackend, GDBSession, _free_port
from jlink_mcp.backends.gui import GUIBackend, GUIProcess
from jlink_mcp.backends.serial import SerialBackend
from jlink_mcp.models import DeviceSelector, TargetCore, TargetState
from jlink_mcp.runner import ProcessRunner

from conftest import make_result


COMMANDER_962 = """
SEGGER J-Link Commander V9.62 (Compiled Jul  1 2026 12:00:00)
DLL version V9.62, compiled Jul 1 2026
Firmware: J-Link EDU Mini V2 compiled Jun 25 2026 10:00:00
Hardware version: V2.00
S/N: 000802008248
License(s): FlashBP, GDB
VTref=3.284V
Found SWD-DP with ID 0x6BA02477
DPIDR: 0x6BA02477
Cortex-M7 identified.
CPUID register: 0x411FC271
PC = 08012345, CycleCnt = 00000000
R0 = 12345678 R1 = DEADBEEF
IPSR = 3 (HardFault)
20000000 = 11223344 AABBCCDD
Breakpoint handle: 2
Watchpoint index # 3
VerifyBin: verified O.K.
"""


def test_commander_962_golden_parser() -> None:
    parsed = parse_commander_output(COMMANDER_962)
    assert parsed == {
        "target_voltage": 3.284,
        "core": "Cortex-M7",
        "probe_serial": "000802008248",
        "firmware": "J-Link EDU Mini V2 compiled Jun 25 2026 10:00:00",
        "hardware_version": "V2.00",
        "licenses": ["FlashBP", "GDB"],
        "cpuid": "0x411FC271",
        "dpidr": "0x6BA02477",
        "pc": "0x08012345",
        "exception": "HardFault",
        "memory": [
            {"address": "0x20000000", "values": ["0x11223344", "0xAABBCCDD"]}
        ],
        "registers": {
            "PC": "0x08012345",
            "CYCLECNT": "0x00000000",
            "R0": "0x12345678",
            "R1": "0xDEADBEEF",
        },
        "breakpoint_handle": 2,
        "watchpoint_handle": 3,
        "commander_version": "9.62",
        "dll_version": "9.62",
        "flash_verified": True,
        "connected": True,
        "registers_present": True,
    }


@pytest.mark.parametrize(
    ("output", "connected"),
    [("Cannot connect to target", False), ("Could not connect", False), ("noise", None)],
)
def test_commander_malformed_and_disconnect(output: str, connected: bool | None) -> None:
    parsed = parse_commander_output(output)
    assert parsed["flash_verified"] is False
    assert parsed.get("connected") is connected


def test_commander_state_inference() -> None:
    assert _infer_states(["H"])[1] == TargetState.HALTED
    assert _infer_states(["Reset"])[1] == TargetState.RESET
    assert _infer_states(["Go"])[1] == TargetState.RUNNING
    assert _infer_states(["Step"])[1] == TargetState.HALTED
    assert _infer_states(["Mem32 0 1"])[1] == TargetState.UNKNOWN


@pytest.mark.asyncio
async def test_process_runner_success_failure_timeout_and_truncation(tmp_path: Path) -> None:
    runner = ProcessRunner(max_output_bytes=16)
    ok = await runner.run(
        [sys.executable, "-c", "import sys; print('x'*100); print('err', file=sys.stderr)"],
        backend="test",
        cwd=tmp_path,
    )
    assert ok.ok
    assert len(ok.stdout.encode()) == 16
    assert ok.stderr == "err\n"
    failed = await runner.run(
        [sys.executable, "-c", "raise SystemExit(7)"], backend="test"
    )
    assert failed.return_code == 7
    timed = await runner.run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        backend="test",
        timeout=0.05,
    )
    assert timed.timed_out
    assert not timed.ok
    with pytest.raises(ValueError):
        await runner.run([], backend="test")


@pytest.mark.asyncio
async def test_process_runner_cancellation_cleans_process_group() -> None:
    runner = ProcessRunner()
    task = asyncio.create_task(
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            backend="test",
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_process_runner_timeout_kills_descendant_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    program = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"open({str(pid_file)!r},'w').write(str(p.pid)); "
        "time.sleep(30)"
    )
    result = await ProcessRunner().run(
        [sys.executable, "-c", program], backend="cleanup", timeout=0.1
    )
    assert result.timed_out
    child_pid = int(pid_file.read_text())
    for _ in range(20):
        if not psutil.pid_exists(child_pid):
            break
        await asyncio.sleep(0.01)
    assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE


def _fake_executable(settings, name: str) -> Path:
    path = settings.segger_root / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.asyncio
async def test_commander_backend_command_file_and_argv(settings) -> None:
    executable = _fake_executable(settings, "JLinkExe")
    captured = {}

    class Runner:
        async def run(self, argv, **kwargs):
            captured["argv"] = [str(item) for item in argv]
            captured.update(kwargs)
            return make_result(stdout=COMMANDER_962, backend="jlink-commander")

    backend = CommanderBackend(settings, Runner())
    selector = DeviceSelector(probe_serial="000802008248", core=TargetCore.M4)
    result = await backend.execute(["H", "Mem32 0x20000000 1"], selector=selector)
    assert result.parsed["connected"]
    assert captured["argv"][0] == str(executable)
    assert "STM32H747XI_M4" in captured["argv"]
    command_file = Path(result.evidence_paths[0])
    assert command_file.read_text() == "H\nMem32 0x20000000 1\nExit\n"
    assert command_file.stat().st_mode & 0o777 == 0o600
    listed = await backend.probe_list()
    assert listed.ok


@pytest.mark.asyncio
async def test_application_backend_allowlist_and_confinement(settings) -> None:
    _fake_executable(settings, "JLinkRTTLoggerExe")
    calls = []

    class Runner:
        async def run(self, argv, **kwargs):
            calls.append(([str(x) for x in argv], kwargs))
            return make_result()

    backend = ApplicationBackend(settings, Runner())
    assert (await backend.execute("JLinkRTTLoggerExe", ["-Device", "x"])).ok
    assert calls[0][0][0].endswith("JLinkRTTLoggerExe")
    with pytest.raises(ValueError, match="unsupported"):
        await backend.execute("sh", ["-c", "id"])


@pytest.mark.asyncio
async def test_serial_backend_validation_parsing_and_error(monkeypatch) -> None:
    backend = SerialBackend()
    monkeypatch.setattr(
        backend,
        "_exchange_sync",
        lambda *args: b'{"ok":true}\nplain\xff\n',
    )
    result = await backend.exchange("/dev/ttyACM0", write="PING", until="ok")
    assert result.ok
    assert result.parsed["records"][0] == {"ok": True}
    assert "plain" in result.parsed["records"][1]["text"]
    monkeypatch.setattr(
        backend,
        "_exchange_sync",
        lambda *args: (_ for _ in ()).throw(OSError("gone")),
    )
    assert not (await backend.exchange("/dev/gone")).ok
    for kwargs in (
        {"baudrate": 1},
        {"duration": 0.01},
        {"write": "\x00"},
        {"write": "x" * 4097},
    ):
        with pytest.raises(ValueError):
            await backend.exchange("/dev/null", **kwargs)


class FakeGDB:
    def __init__(self, response=None, error=None):
        self.response = response or [{"message": "done", "payload": {}}]
        self.error = error
        self.commands = []
        self.exited = False

    def write(self, command, timeout_sec=0):
        self.commands.append(command)
        if self.error:
            raise self.error
        return self.response

    def exit(self):
        self.exited = True


class FakeProcess:
    pid = 99999999
    returncode = 0

    async def wait(self):
        return self.returncode


def _gdb_session(gdb=None) -> GDBSession:
    return GDBSession(
        session_id="session",
        selector=DeviceSelector(
            probe_serial="000802008248", board_serial="BOARD", core=TargetCore.M7
        ),
        gdb_port=1111,
        swo_port=2222,
        telnet_port=3333,
        rtt_port=4444,
        server=FakeProcess(),
        gdb=gdb or FakeGDB(),
        created_at=datetime.now(UTC),
        elf_path=None,
    )


@pytest.mark.asyncio
async def test_gdb_command_states_errors_timeout_and_info(settings) -> None:
    backend = GDBBackend(settings, ProcessRunner())
    backend._sessions["session"] = _gdb_session()
    result = await backend.command("session", "-exec-continue")
    assert result.ok and result.target_state_after == TargetState.RUNNING
    assert (await backend.command("session", "-exec-next")).target_state_after == TargetState.HALTED
    backend._sessions["session"].gdb = FakeGDB(response=[{"message": "error"}])
    assert (await backend.command("session", "-thread-info")).return_code == 1
    backend._sessions["session"].gdb = FakeGDB(error=GdbTimeoutError("timeout"))
    timed = await backend.command("session", "-thread-info", timeout=0.01)
    assert timed.timed_out and "timeout" in timed.stderr
    assert backend.session_info("session")["rtt_port"] == 4444
    with pytest.raises(ValueError, match="unknown GDB"):
        backend.session_info("missing")
    await backend.stop("session")
    await backend.stop("missing")


@pytest.mark.asyncio
async def test_gdb_capture_port_success_and_failure(settings) -> None:
    backend = GDBBackend(settings, ProcessRunner())
    received = bytearray()

    async def handler(reader, writer):
        received.extend(await reader.read(32))
        writer.write(b"RTT DATA")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    session = _gdb_session()
    session.rtt_port = port
    backend._sessions["session"] = session
    try:
        result = await backend.capture_port(
            "session", "rtt", duration=0.05, write="PING"
        )
    finally:
        server.close()
        await server.wait_closed()
    assert result.ok and result.stdout == "RTT DATA" and received == b"PING"
    session.swo_port = _free_port()
    assert not (await backend.capture_port("session", "swo", duration=0.05)).ok
    with pytest.raises(ValueError):
        await backend.capture_port("session", "bad")
    with pytest.raises(ValueError):
        await backend.capture_port("session", "rtt", duration=0.01)


@pytest.mark.asyncio
async def test_gui_backend_controls_ocr_screenshot_and_image_match(settings, monkeypatch) -> None:
    backend = GUIBackend(settings, ProcessRunner())
    process = FakeProcess()
    backend._sessions["gui"] = GUIProcess(
        session_id="gui",
        application="JLinkConfigExe",
        process=process,
        started_at=datetime.now(UTC),
    )
    assert backend.session_info("gui")["running"] is False
    calls = []

    class Runner:
        async def run(self, argv, **kwargs):
            calls.append(([str(x) for x in argv], kwargs))
            destination = Path(argv[-1])
            if destination.suffix == ".png":
                image = np.zeros((40, 40, 3), dtype=np.uint8)
                image[10:20, 12:22] = 255
                cv2.imwrite(str(destination), image)
            result = make_result()
            if destination.suffix == ".png":
                result.evidence_paths.append(str(destination))
            return result

    backend.runner = Runner()
    monkeypatch.setattr("jlink_mcp.backends.gui.shutil.which", lambda name: f"/usr/bin/{name}")
    assert (await backend.keys("gui", "ctrl+l")).ok
    assert (await backend.click("gui", 1, 2)).ok
    screenshot = await backend.screenshot("gui")
    assert screenshot.evidence_paths
    assert (await backend.ocr(Path(screenshot.evidence_paths[0]))).ok
    source = cv2.imread(screenshot.evidence_paths[0])
    template = settings.workspace_root / "template.png"
    cv2.imwrite(str(template), source[10:20, 12:22])
    matched = await backend.image_match("gui", template, threshold=0.8)
    assert matched.parsed["matched"]
    with pytest.raises(ValueError):
        await backend.image_match("gui", template, threshold=2)
    too_large = settings.workspace_root / "large.png"
    cv2.imwrite(str(too_large), np.zeros((100, 100, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="larger"):
        await backend.image_match("gui", too_large)
    with pytest.raises(ValueError, match="unknown GUI"):
        await backend.keys("missing", "a")
    await backend.stop("gui")
