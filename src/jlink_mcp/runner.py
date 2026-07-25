"""Deterministic, shell-free asynchronous subprocess execution."""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .models import CommandResult, TargetState


class ProcessRunner:
    def __init__(self, *, max_output_bytes: int = 4_000_000) -> None:
        self.max_output_bytes = max_output_bytes

    async def run(
        self,
        argv: Sequence[str | os.PathLike[str]],
        *,
        backend: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        state_before: TargetState = TargetState.UNKNOWN,
        state_after: TargetState = TargetState.UNKNOWN,
    ) -> CommandResult:
        command = [os.fspath(item) for item in argv]
        if not command or not command[0]:
            raise ValueError("command must not be empty")

        started = datetime.now(UTC)
        operation_id = str(uuid.uuid4())
        child_env = os.environ.copy()
        if env:
            child_env.update({str(key): str(value) for key, value in env.items()})

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd else None,
            env=child_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        timed_out = False
        return_code: int | None
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            return_code = process.returncode
        except asyncio.CancelledError:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            raise
        except TimeoutError:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout_raw, stderr_raw = await asyncio.wait_for(
                    process.communicate(), timeout=3
                )
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout_raw, stderr_raw = await process.communicate()
            return_code = process.returncode

        stdout_raw = stdout_raw[: self.max_output_bytes]
        stderr_raw = stderr_raw[: self.max_output_bytes]
        finished = datetime.now(UTC)
        return CommandResult(
            operation_id=operation_id,
            backend=backend,
            command=command,
            started_at=started,
            finished_at=finished,
            duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
            return_code=return_code,
            timed_out=timed_out,
            stdout=stdout_raw.decode("utf-8", errors="replace"),
            stderr=stderr_raw.decode("utf-8", errors="replace"),
            target_state_before=state_before,
            target_state_after=state_after,
        )
