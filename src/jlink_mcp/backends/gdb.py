"""Managed J-Link GDB Server and GDB/MI sessions."""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pygdbmi.gdbcontroller import GdbController
from pygdbmi.IoManager import GdbTimeoutError

from ..config import Settings
from ..models import CommandResult, DeviceSelector, TargetState
from ..profiles import TargetRegistry
from ..runner import ProcessRunner
from ..security import validate_gdb_command


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(slots=True)
class GDBSession:
    session_id: str
    selector: DeviceSelector
    gdb_port: int
    swo_port: int
    telnet_port: int
    rtt_port: int
    server: asyncio.subprocess.Process
    gdb: GdbController
    created_at: datetime
    elf_path: Path | None


class GDBBackend:
    name = "jlink-gdb"

    def __init__(
        self, settings: Settings, runner: ProcessRunner, targets: TargetRegistry
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.targets = targets
        self._sessions: dict[str, GDBSession] = {}
        self._guard = asyncio.Lock()

    async def start(
        self,
        selector: DeviceSelector,
        *,
        elf_path: Path | None = None,
        timeout: float = 15,
    ) -> str:
        executable = self.settings.segger_executable("JLinkGDBServerCLExe")
        gdb_port, swo_port, telnet_port, rtt_port = (
            _free_port(),
            _free_port(),
            _free_port(),
            _free_port(),
        )
        argv = [
            str(executable),
            "-select",
            f"USB={selector.probe_serial}",
            "-device",
            self.targets.jlink_device(selector.target_profile, selector.core),
            "-if",
            selector.interface,
            "-speed",
            str(selector.speed_khz),
            "-port",
            str(gdb_port),
            "-swoport",
            str(swo_port),
            "-telnetport",
            str(telnet_port),
            "-RTTTelnetPort",
            str(rtt_port),
            "-nogui",
            "1",
            "-silent",
            "-noir",
        ]
        server = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self.settings.workspace_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        await self._wait_for_port(gdb_port, server, timeout=timeout)
        gdb = GdbController(
            command=[self.settings.gdb_client, "--interpreter=mi2", "--nx", "--quiet"]
        )
        if elf_path:
            gdb.write(f'-file-exec-and-symbols "{elf_path}"', timeout_sec=timeout)
        response = gdb.write(
            f"-target-select remote 127.0.0.1:{gdb_port}", timeout_sec=timeout
        )
        if any(
            item.get("message") == "error" for item in response if isinstance(item, dict)
        ):
            gdb.exit()
            server.terminate()
            await server.wait()
            raise RuntimeError(f"GDB target connection failed: {response!r}")
        session_id = str(uuid.uuid4())
        session = GDBSession(
            session_id=session_id,
            selector=selector,
            gdb_port=gdb_port,
            swo_port=swo_port,
            telnet_port=telnet_port,
            rtt_port=rtt_port,
            server=server,
            gdb=gdb,
            created_at=datetime.now(UTC),
            elf_path=elf_path,
        )
        async with self._guard:
            self._sessions[session_id] = session
        return session_id

    async def _wait_for_port(
        self, port: int, process: asyncio.subprocess.Process, *, timeout: float
    ) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                output = ""
                if process.stdout:
                    output = (await process.stdout.read()).decode(errors="replace")
                raise RuntimeError(f"GDB Server exited before listening: {output}")
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(0.1)
        process.terminate()
        await process.wait()
        raise TimeoutError(f"GDB Server did not listen on port {port}")

    async def command(
        self, session_id: str, command: str, *, timeout: float = 30
    ) -> CommandResult:
        validate_gdb_command(command)
        session = self._session(session_id)
        started = datetime.now(UTC)
        timed_out = False
        error = ""
        try:
            response = await asyncio.to_thread(
                session.gdb.write, command, timeout_sec=timeout
            )
        except GdbTimeoutError as exc:
            response = []
            timed_out = True
            error = str(exc)
        finished = datetime.now(UTC)
        errors = [
            item for item in response if isinstance(item, dict) and item.get("message") == "error"
        ]
        state_after = TargetState.UNKNOWN
        if command.startswith(("-exec-continue", "continue")):
            state_after = TargetState.RUNNING
        elif command.startswith(("-exec-step", "-exec-next", "step", "next")):
            state_after = TargetState.HALTED
        return CommandResult(
            operation_id=str(uuid.uuid4()),
            session_id=session_id,
            backend=self.name,
            command=[command],
            started_at=started,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            return_code=None if timed_out else (1 if errors else 0),
            timed_out=timed_out,
            stdout="",
            stderr=error,
            parsed={"mi": response, "session_id": session_id},
            target_state_after=state_after,
            probe_identity={"serial": session.selector.probe_serial},
            target_identity={
                "board_serial": session.selector.board_serial,
                "target_profile": session.selector.target_profile,
                "core": session.selector.core,
            },
        )

    async def capture_port(
        self,
        session_id: str,
        channel: str,
        *,
        duration: float = 2.0,
        write: str | None = None,
    ) -> CommandResult:
        """Capture a managed GDB Server auxiliary TCP channel."""

        if channel not in {"swo", "semihosting", "rtt"}:
            raise ValueError("channel must be swo, semihosting, or rtt")
        if not 0.05 <= duration <= 300:
            raise ValueError("duration must be between 0.05 and 300 seconds")
        session = self._session(session_id)
        port = {
            "swo": session.swo_port,
            "semihosting": session.telnet_port,
            "rtt": session.rtt_port,
        }[channel]
        started = datetime.now(UTC)
        chunks: list[bytes] = []
        error = ""
        return_code = 0
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            if write:
                writer.write(write.encode("utf-8"))
                await writer.drain()
            deadline = asyncio.get_running_loop().time() + duration
            while asyncio.get_running_loop().time() < deadline:
                remaining = deadline - asyncio.get_running_loop().time()
                try:
                    chunk = await asyncio.wait_for(
                        reader.read(65536), timeout=min(0.2, max(0.01, remaining))
                    )
                except TimeoutError:
                    continue
                if not chunk:
                    break
                chunks.append(chunk)
            writer.close()
            await writer.wait_closed()
        except OSError as exc:
            error = str(exc)
            return_code = 1
        finished = datetime.now(UTC)
        output = b"".join(chunks).decode("utf-8", errors="replace")
        return CommandResult(
            operation_id=str(uuid.uuid4()),
            session_id=session_id,
            backend=f"{self.name}-{channel}",
            command=["tcp-capture", "127.0.0.1", str(port)],
            started_at=started,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            return_code=return_code,
            stdout=output,
            stderr=error,
            parsed={
                "session_id": session_id,
                "channel": channel,
                "port": port,
                "bytes": len(b"".join(chunks)),
            },
            probe_identity={"serial": session.selector.probe_serial},
            target_identity={
                "board_serial": session.selector.board_serial,
                "target_profile": session.selector.target_profile,
                "core": session.selector.core,
            },
        )

    async def stop(self, session_id: str, *, resume: bool = True) -> None:
        async with self._guard:
            session = self._sessions.pop(session_id, None)
        if not session:
            return
        try:
            if resume:
                session.gdb.write("-exec-continue", timeout_sec=2)
        except Exception:
            pass
        try:
            session.gdb.exit()
        finally:
            if session.server.returncode is None:
                try:
                    os.killpg(session.server.pid, 15)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(session.server.wait(), timeout=3)
                except TimeoutError:
                    try:
                        os.killpg(session.server.pid, 9)
                    except ProcessLookupError:
                        pass
                    await session.server.wait()

    async def stop_all(self) -> None:
        for session_id in list(self._sessions):
            await self.stop(session_id)

    def session_info(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        return {
            "session_id": session.session_id,
            "probe_serial": session.selector.probe_serial,
            "gdb_port": session.gdb_port,
            "swo_port": session.swo_port,
            "telnet_port": session.telnet_port,
            "rtt_port": session.rtt_port,
            "created_at": session.created_at.isoformat(),
            "elf_path": str(session.elf_path) if session.elf_path else None,
        }

    def _session(self, session_id: str) -> GDBSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ValueError(f"unknown GDB session: {session_id}") from exc
