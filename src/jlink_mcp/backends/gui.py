"""Isolated Xvfb SEGGER GUI automation backend."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import cv2

from ..config import Settings
from ..models import CommandResult
from ..runner import ProcessRunner
from ..security import validate_application_args


@dataclass(slots=True)
class GUIProcess:
    session_id: str
    application: str
    process: asyncio.subprocess.Process
    started_at: datetime


class GUIBackend:
    name = "segger-gui"
    allowed_applications: ClassVar[set[str]] = {
        "JFlashExe",
        "JFlashLiteExe",
        "JFlashSPIExe",
        "JLinkConfigExe",
        "JLinkGDBServerExe",
        "JLinkLicenseManagerExe",
        "JLinkRegistrationExe",
        "JLinkRemoteServerExe",
        "JLinkRTTViewerExe",
        "JLinkSWOViewerExe",
        "JMemExe",
        "JScopeExe",
    }

    def __init__(self, settings: Settings, runner: ProcessRunner) -> None:
        self.settings = settings
        self.runner = runner
        self._xvfb: asyncio.subprocess.Process | None = None
        self._sessions: dict[str, GUIProcess] = {}

    async def ensure_display(self) -> None:
        if self._xvfb and self._xvfb.returncode is None:
            return
        display_number = self.settings.display.removeprefix(":").split(".", 1)[0]
        if Path(f"/tmp/.X11-unix/X{display_number}").exists():
            # The optional noVNC entrypoint owns this isolated display.
            return
        xvfb = shutil.which("Xvfb")
        if not xvfb:
            raise RuntimeError("Xvfb is not installed")
        self._xvfb = await asyncio.create_subprocess_exec(
            xvfb,
            self.settings.display,
            "-screen",
            "0",
            "1280x1024x24",
            "-nolisten",
            "tcp",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        await asyncio.sleep(0.4)
        if self._xvfb.returncode is not None:
            stderr = ""
            if self._xvfb.stderr:
                stderr = (await self._xvfb.stderr.read()).decode(errors="replace")
            raise RuntimeError(f"Xvfb failed to start: {stderr}")

    async def launch(self, application: str, args: list[str]) -> str:
        if application not in self.allowed_applications:
            raise ValueError(f"unsupported GUI application: {application}")
        validated = validate_application_args(args, self.settings)
        await self.ensure_display()
        executable = self.settings.segger_executable(application)
        env = os.environ.copy()
        env["DISPLAY"] = self.settings.display
        process = await asyncio.create_subprocess_exec(
            str(executable),
            *validated,
            cwd=str(self.settings.workspace_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        await asyncio.sleep(0.3)
        if process.returncode is not None:
            stdout, stderr = await process.communicate()
            detail = (stdout + stderr).decode(errors="replace").strip()
            raise RuntimeError(
                f"{application} exited during GUI startup with code "
                f"{process.returncode}: {detail}"
            )
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = GUIProcess(
            session_id=session_id,
            application=application,
            process=process,
            started_at=datetime.now(UTC),
        )
        return session_id

    def session_info(self, session_id: str) -> dict[str, object]:
        session = self._session(session_id)
        return {
            "session_id": session.session_id,
            "application": session.application,
            "running": session.process.returncode is None,
            "return_code": session.process.returncode,
            "pid": session.process.pid,
            "started_at": session.started_at.isoformat(),
            "display": self.settings.display,
        }

    async def keys(self, session_id: str, keys: str) -> CommandResult:
        self._session(session_id)
        xdotool = shutil.which("xdotool")
        if not xdotool:
            raise RuntimeError("xdotool is not installed")
        return await self.runner.run(
            [xdotool, "key", "--clearmodifiers", keys],
            backend=self.name,
            env={"DISPLAY": self.settings.display},
            timeout=10,
        )

    async def click(self, session_id: str, x: int, y: int) -> CommandResult:
        self._session(session_id)
        xdotool = shutil.which("xdotool")
        if not xdotool:
            raise RuntimeError("xdotool is not installed")
        return await self.runner.run(
            [xdotool, "mousemove", str(x), str(y), "click", "1"],
            backend=self.name,
            env={"DISPLAY": self.settings.display},
            timeout=10,
        )

    async def screenshot(self, session_id: str) -> CommandResult:
        self._session(session_id)
        destination = (
            self.settings.state_root
            / "screenshots"
            / f"{session_id}-{uuid.uuid4()}.png"
        )
        if scrot := shutil.which("scrot"):
            argv = [scrot, str(destination)]
        elif import_tool := shutil.which("import"):
            argv = [import_tool, "-window", "root", str(destination)]
        else:
            raise RuntimeError("neither scrot nor ImageMagick import is installed")
        result = await self.runner.run(
            argv,
            backend=self.name,
            env={"DISPLAY": self.settings.display},
            timeout=15,
        )
        if result.ok and destination.exists():
            result.evidence_paths.append(str(destination))
        return result

    async def ocr(self, screenshot: Path) -> CommandResult:
        tesseract = shutil.which("tesseract")
        if not tesseract:
            raise RuntimeError("tesseract is not installed")
        return await self.runner.run(
            [tesseract, str(screenshot), "stdout"],
            backend=self.name,
            timeout=30,
        )

    async def accessibility_tree(self, session_id: str) -> CommandResult:
        """Return semantic roles, names, states, and hierarchy over AT-SPI."""

        self._session(session_id)
        helper = Path(__file__).resolve().parent.parent / "atspi_snapshot.py"
        if not helper.is_file():
            raise RuntimeError("AT-SPI snapshot helper is missing")
        return await self.runner.run(
            ["/usr/bin/python3", helper],
            backend=f"{self.name}-atspi",
            env={
                "DISPLAY": self.settings.display,
                "DBUS_SESSION_BUS_ADDRESS": os.environ.get(
                    "DBUS_SESSION_BUS_ADDRESS", ""
                ),
            },
            timeout=30,
        )

    async def image_match(
        self,
        session_id: str,
        template: Path,
        *,
        threshold: float = 0.85,
    ) -> CommandResult:
        """Capture the display and locate a version-pinned template image."""

        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        capture = await self.screenshot(session_id)
        started = datetime.now(UTC)
        if not capture.ok or not capture.evidence_paths:
            return capture
        screenshot = Path(capture.evidence_paths[-1])
        source = cv2.imread(str(screenshot), cv2.IMREAD_COLOR)
        needle = cv2.imread(str(template), cv2.IMREAD_COLOR)
        if source is None or needle is None:
            raise ValueError("screenshot or template is not a readable image")
        if needle.shape[0] > source.shape[0] or needle.shape[1] > source.shape[1]:
            raise ValueError("template is larger than the screenshot")
        scores = cv2.matchTemplate(source, needle, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(scores)
        finished = datetime.now(UTC)
        return CommandResult(
            operation_id=str(uuid.uuid4()),
            session_id=session_id,
            backend=f"{self.name}-opencv",
            command=["match-template", str(screenshot), str(template)],
            started_at=started,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            return_code=0,
            parsed={
                "matched": score >= threshold,
                "score": score,
                "threshold": threshold,
                "x": location[0],
                "y": location[1],
                "width": needle.shape[1],
                "height": needle.shape[0],
            },
            evidence_paths=[str(screenshot), str(template)],
        )

    async def stop(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if not session or session.process.returncode is not None:
            return
        try:
            os.killpg(session.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(session.process.wait(), timeout=3)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(session.process.pid, signal.SIGKILL)
            await session.process.wait()

    async def stop_all(self) -> None:
        for session_id in list(self._sessions):
            await self.stop(session_id)
        if self._xvfb and self._xvfb.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._xvfb.pid, signal.SIGTERM)
            await self._xvfb.wait()

    def _session(self, session_id: str) -> GUIProcess:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ValueError(f"unknown GUI session: {session_id}") from exc
