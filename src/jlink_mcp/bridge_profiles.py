"""Mode-0600 loading for named Wi-Fi credentials and BLE passkeys."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from .bridge_models import ProtocolBridgeProfiles


def load_bridge_profiles(path: Path | None) -> ProtocolBridgeProfiles:
    if path is None:
        raise RuntimeError(
            "JLINK_MCP_BRIDGE_PROFILES_FILE is required for named wireless profiles"
        )
    resolved = path.expanduser().resolve(strict=True)
    details = resolved.stat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("bridge profiles path must be a regular file")
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise PermissionError("bridge profiles file must have mode 0600")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("bridge profiles file is not valid JSON") from exc
    return ProtocolBridgeProfiles.model_validate(payload)
