"""Reserved adapter for a separately licensed J-Link SDK."""

from __future__ import annotations

from collections.abc import Sequence

from ..models import CommandResult, DeviceSelector
from .base import DebugBackend


class SDKUnavailable(RuntimeError):
    pass


class SDKBackend(DebugBackend):
    name = "jlink-sdk"

    async def execute(
        self,
        commands: Sequence[str],
        *,
        selector: DeviceSelector | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        raise SDKUnavailable(
            "Direct J-Link SDK integration requires a separately licensed SDK "
            "package containing JLinkARMDLL.h and UM08002."
        )
