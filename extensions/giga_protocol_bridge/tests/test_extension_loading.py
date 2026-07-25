from __future__ import annotations

from pathlib import Path

import pytest

from jlink_mcp.extensions.api import ExtensionError
from jlink_mcp.extensions.loader import ExtensionManager
from jlink_mcp_giga_protocol_bridge.extension import (
    GigaProtocolBridgeExtension,
    _dependencies,
)


class EntryPoint:
    name = "giga_protocol_bridge"

    @staticmethod
    def load():
        return GigaProtocolBridgeExtension


def test_bridge_cannot_load_without_arduino_giga(settings) -> None:
    from jlink_mcp.extensions.api import ArtifactService, ExtensionRegistry, ExtensionServices

    manager = ExtensionManager(
        enabled=["giga_protocol_bridge"],
        config_path=None,
        services=ExtensionServices(
            jlink=object(),
            serial=object(),
            artifacts=ArtifactService(settings, object()),
            audit=object(),
            paths=settings,
            process=object(),
        ),
        registry=ExtensionRegistry(),
        mcp=object(),
        entry_points=[EntryPoint()],
        environ={},
    )
    with pytest.raises(ExtensionError, match="disabled or missing dependencies"):
        manager.load()


def test_packaged_release_retains_authorizing_checksum(tmp_path: Path) -> None:
    checks = {item.name: item for item in _dependencies(tmp_path)}
    assert checks["protocol-bridge-release"].ok
