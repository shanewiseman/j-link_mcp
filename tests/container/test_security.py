from __future__ import annotations

import json
import os
import subprocess

import pytest


ENABLED = os.environ.get("JLINK_MCP_CONTAINER_TEST") == "1"
pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(not ENABLED, reason="set JLINK_MCP_CONTAINER_TEST=1"),
]


def _compose(*args: str) -> str:
    return subprocess.run(
        ["docker", "compose", "-f", "compose.yaml", "-f", "compose.snap.yaml", *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout


def test_container_non_root_restricted_and_no_proprietary_payload() -> None:
    config = _compose("config", "--format", "json")
    service = json.loads(config)["services"]["mcp"]
    assert service.get("privileged", False) is False
    assert set(service["cap_drop"]) >= {"ALL"}
    assert service["read_only"] is True
    assert service["network_mode"] == "host"
    assert service["environment"]["JLINK_MCP_HOST"] == "127.0.0.1"
    assert any("/dev/bus/usb" in volume["target"] for volume in service["volumes"])
    assert all(volume["type"] == "bind" for volume in service["volumes"])

    user = _compose("exec", "-T", "mcp", "id", "-u").strip()
    assert user != "0"
    status = _compose("exec", "-T", "mcp", "sh", "-c", "grep '^CapEff:' /proc/self/status")
    assert status.strip().endswith("0000000000000000")
    mounts = _compose("exec", "-T", "mcp", "sh", "-c", "grep ' / ' /proc/mounts")
    assert "ro" in mounts.split()[3].split(",")
    files = _compose(
        "exec",
        "-T",
        "mcp",
        "sh",
        "-c",
        "find /opt/jlink-mcp -type f -print",
    )
    assert "JLinkExe" not in files
    assert "/opt/segger" not in files
