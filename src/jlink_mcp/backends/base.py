"""Backend interface contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ..models import CommandResult, DeviceSelector


class DebugBackend(ABC):
    name: str

    @abstractmethod
    async def execute(
        self,
        commands: Sequence[str],
        *,
        selector: DeviceSelector | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        raise NotImplementedError
