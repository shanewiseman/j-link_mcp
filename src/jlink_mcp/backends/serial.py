"""Stable-identity USB serial command and capture backend."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime

import serial

from ..models import CommandResult


class SerialBackend:
    name = "usb-serial"

    async def exchange(
        self,
        port: str,
        *,
        write: str | None = None,
        baudrate: int = 115200,
        duration: float = 2.0,
        until: str | None = None,
    ) -> CommandResult:
        if not 50 <= baudrate <= 4_000_000:
            raise ValueError("baudrate is outside the supported range")
        if not 0.05 <= duration <= 300:
            raise ValueError("duration must be between 0.05 and 300 seconds")
        if write is not None and ("\x00" in write or len(write) > 4096):
            raise ValueError("serial request is invalid or too large")

        started = datetime.now(UTC)
        try:
            raw = await asyncio.to_thread(
                self._exchange_sync,
                port,
                write,
                baudrate,
                duration,
                until,
            )
            error = ""
            return_code = 0
        except (OSError, serial.SerialException) as exc:
            raw = b""
            error = str(exc)
            return_code = 1
        finished = datetime.now(UTC)
        text = raw.decode("utf-8", errors="replace")
        records: list[object] = []
        for line in text.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"text": line})
        return CommandResult(
            operation_id=str(uuid.uuid4()),
            backend=self.name,
            command=["serial", port, str(baudrate), write or ""],
            started_at=started,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            return_code=return_code,
            stdout=text,
            stderr=error,
            parsed={"port": port, "baudrate": baudrate, "records": records},
        )

    async def exchange_binary(
        self,
        port: str,
        *,
        write: bytes,
        baudrate: int = 115200,
        duration: float = 5.0,
        idle_after_data: float = 0.075,
    ) -> tuple[CommandResult, bytes]:
        """Exchange opaque bytes without putting payloads in commands or logs."""

        if not 50 <= baudrate <= 4_000_000:
            raise ValueError("baudrate is outside the supported range")
        if not 0.05 <= duration <= 300:
            raise ValueError("duration must be between 0.05 and 300 seconds")
        if not 0.01 <= idle_after_data <= 1:
            raise ValueError("idle_after_data must be between 0.01 and 1 second")
        if not write or len(write) > 128 * 1024:
            raise ValueError("binary serial request is empty or too large")

        request_sha256 = hashlib.sha256(write).hexdigest()
        started = datetime.now(UTC)
        try:
            raw = await asyncio.to_thread(
                self._exchange_binary_sync,
                port,
                write,
                baudrate,
                duration,
                idle_after_data,
            )
            error = ""
            return_code = 0
        except (OSError, serial.SerialException) as exc:
            raw = b""
            error = f"{type(exc).__name__}: binary serial exchange failed"
            return_code = 1
        finished = datetime.now(UTC)
        result = CommandResult(
            operation_id=str(uuid.uuid4()),
            backend=self.name,
            command=[
                "serial-binary",
                port,
                str(baudrate),
                f"sha256:{request_sha256}",
            ],
            started_at=started,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            return_code=return_code,
            stdout="",
            stderr=error,
            parsed={
                "port": port,
                "baudrate": baudrate,
                "request_bytes": len(write),
                "request_sha256": request_sha256,
                "response_bytes": len(raw),
                "response_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        return result, raw

    @staticmethod
    def _exchange_sync(
        port: str,
        write: str | None,
        baudrate: int,
        duration: float,
        until: str | None,
    ) -> bytes:
        deadline = time.monotonic() + duration
        chunks: list[bytes] = []
        with serial.Serial(port, baudrate=baudrate, timeout=0.05) as stream:
            stream.reset_input_buffer()
            if write is not None:
                payload = write.encode("utf-8")
                if not payload.endswith(b"\n"):
                    payload += b"\n"
                stream.write(payload)
                stream.flush()
            while time.monotonic() < deadline:
                chunk = stream.read(max(1, stream.in_waiting))
                if chunk:
                    chunks.append(chunk)
                    if until and until.encode("utf-8") in b"".join(chunks):
                        break
        return b"".join(chunks)

    @staticmethod
    def _exchange_binary_sync(
        port: str,
        write: bytes,
        baudrate: int,
        duration: float,
        idle_after_data: float,
    ) -> bytes:
        deadline = time.monotonic() + duration
        last_data: float | None = None
        chunks: list[bytes] = []
        with serial.Serial(port, baudrate=baudrate, timeout=0.02) as stream:
            stream.reset_input_buffer()
            stream.write(write)
            stream.flush()
            while time.monotonic() < deadline:
                chunk = stream.read(max(1, stream.in_waiting))
                if chunk:
                    chunks.append(chunk)
                    last_data = time.monotonic()
                elif last_data is not None and time.monotonic() - last_data >= idle_after_data:
                    break
        return b"".join(chunks)
