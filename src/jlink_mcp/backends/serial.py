"""Stable-identity USB serial command and capture backend."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import serial

from ..models import CommandResult

MAX_BINARY_RESPONSE_BYTES = 128 * 1024


class SerialResponseLimitExceeded(RuntimeError):
    """A binary peer sent more data than the bounded response buffer permits."""

    def __init__(self, *, observed_bytes: int, limit_bytes: int) -> None:
        self.observed_bytes = observed_bytes
        self.limit_bytes = limit_bytes
        super().__init__(
            f"binary serial response exceeded the {limit_bytes}-byte limit"
        )


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
            raw = await self._run_sync_to_completion(
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
        overflow: SerialResponseLimitExceeded | None = None
        try:
            raw = await self._run_sync_to_completion(
                self._exchange_binary_sync,
                port,
                write,
                baudrate,
                duration,
                idle_after_data,
            )
            error = ""
            return_code = 0
        except SerialResponseLimitExceeded as exc:
            raw = b""
            overflow = exc
            error = f"{type(exc).__name__}: {exc}"
            return_code = 1
        except (OSError, serial.SerialException) as exc:
            raw = b""
            error = f"{type(exc).__name__}: binary serial exchange failed"
            return_code = 1
        finished = datetime.now(UTC)
        response_metadata: dict[str, object] = {
            "response_bytes": len(raw),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
        }
        if overflow is not None:
            response_metadata = {
                "response_bytes": 0,
                "response_overflow": True,
                "response_observed_bytes": overflow.observed_bytes,
                "response_limit_bytes": overflow.limit_bytes,
            }
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
                **response_metadata,
            },
        )
        return result, raw

    @staticmethod
    async def _run_sync_to_completion(
        function: Callable[..., bytes], *args: object
    ) -> bytes:
        """Keep a cancelled caller attached until its serial worker has closed."""

        worker = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # asyncio.to_thread cannot stop a running thread. Waiting here keeps
            # higher-level serialization locks held until the port context exits.
            current_task = asyncio.current_task()
            if current_task is not None:
                while current_task.cancelling():
                    current_task.uncancel()
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    if current_task is not None:
                        while current_task.cancelling():
                            current_task.uncancel()
                    continue
            if not worker.cancelled():
                # Retrieve an exception so cancellation cannot leave task noise.
                worker.exception()
            raise

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
        response_bytes = 0
        with serial.Serial(port, baudrate=baudrate, timeout=0.02) as stream:
            stream.reset_input_buffer()
            stream.write(write)
            stream.flush()
            while time.monotonic() < deadline:
                remaining_bytes = MAX_BINARY_RESPONSE_BYTES - response_bytes
                chunk = stream.read(min(max(1, stream.in_waiting), remaining_bytes + 1))
                if chunk:
                    observed_bytes = response_bytes + len(chunk)
                    if observed_bytes > MAX_BINARY_RESPONSE_BYTES:
                        raise SerialResponseLimitExceeded(
                            observed_bytes=observed_bytes,
                            limit_bytes=MAX_BINARY_RESPONSE_BYTES,
                        )
                    response_bytes = observed_bytes
                    chunks.append(chunk)
                    last_data = time.monotonic()
                elif (
                    last_data is not None
                    and time.monotonic() - last_data >= idle_after_data
                ):
                    break
        return b"".join(chunks)
