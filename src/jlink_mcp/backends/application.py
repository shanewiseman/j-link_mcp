"""Shell-free access to installed SEGGER command-line applications."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..discovery import SEGGER_TOOLS
from ..models import CommandResult
from ..runner import ProcessRunner
from ..security import validate_application_args


class ApplicationBackend:
    """Run a finite, allowlisted SEGGER CLI operation.

    Interactive/persistent applications have dedicated managed backends. This
    adapter is the raw escape hatch for finite command-line modes and therefore
    never invokes a shell.
    """

    name = "segger-application"
    allowed_applications = frozenset(SEGGER_TOOLS)

    def __init__(self, settings: Settings, runner: ProcessRunner) -> None:
        self.settings = settings
        self.runner = runner

    async def execute(
        self,
        application: str,
        args: list[str],
        *,
        timeout: float | None = None,
    ) -> CommandResult:
        if application not in self.allowed_applications:
            raise ValueError(f"unsupported SEGGER application: {application}")
        executable = self.settings.segger_executable(application)
        validated = validate_application_args(args, self.settings)
        return await self.runner.run(
            [executable, *validated],
            backend=f"{self.name}:{application}",
            cwd=self.settings.workspace_root,
            timeout=timeout or self.settings.default_timeout_seconds,
        )
