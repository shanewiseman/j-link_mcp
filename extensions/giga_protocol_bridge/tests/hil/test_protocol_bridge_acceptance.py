from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from .support import session, unpack

PROTOCOL_HIL_ENABLED = os.environ.get("JLINK_MCP_PROTOCOL_HIL") == "1"
PROTOCOLS = ("spi", "i2c", "uart", "can", "usb", "wifi", "ble", "gpio")
TOOLS = {
    "protocol_bridge_control",
    "protocol_bridge_exchange",
    "protocol_bridge_receive",
}
MISSING_REASONS = {
    "spi": "wired SPI responder or loopback fixture was not configured",
    "i2c": "wired I2C target/echo fixture was not configured",
    "uart": "wired UART TX/RX loopback or peer was not configured",
    "can": "two terminated external CAN transceivers and a CAN peer were not configured",
    "usb": "supported non-hub USB echo device on the GIGA USB-A host port was not configured",
    "wifi": "mode-0600 Wi-Fi profile and reachable TCP/UDP echo peer were not configured",
    "ble": "reachable BLE central-role GATT echo peripheral was not configured",
    "gpio": "safe GPIO output-to-input jumper fixture was not configured",
}


pytestmark = [
    pytest.mark.hil,
    pytest.mark.hardware,
    pytest.mark.destructive,
    pytest.mark.skipif(
        not PROTOCOL_HIL_ENABLED,
        reason="set JLINK_MCP_PROTOCOL_HIL=1 and JLINK_MCP_PROTOCOL_HIL_FIXTURES",
    ),
]


def _fixtures() -> dict[str, dict]:
    raw_path = os.environ.get("JLINK_MCP_PROTOCOL_HIL_FIXTURES")
    if not raw_path:
        return {}
    path = Path(raw_path).expanduser().resolve(strict=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) - set(PROTOCOLS):
        raise ValueError(
            "protocol HIL fixture JSON must map only supported protocol names"
        )
    for protocol, case in payload.items():
        if not isinstance(case, dict) or not isinstance(case.get("actions"), list):
            raise TypeError(f"{protocol} fixture requires an actions list")
    return payload


def _check_expectation(protocol: str, result: dict, expected: dict) -> None:
    if "data_base64" in expected:
        assert result["data_base64"] == expected["data_base64"], protocol
    if "minimum_byte_count" in expected:
        assert result["byte_count"] >= expected["minimum_byte_count"], protocol
    for name, value in expected.get("metadata", {}).items():
        assert result["metadata"].get(name) == value, protocol


@pytest.mark.asyncio
async def test_protocol_bridge_fixture_matrix_and_restore(selector, capsys) -> None:
    """Run configured physical cases through MCP and always restore full flash."""

    fixtures = _fixtures()
    statuses = {
        protocol: {
            "state": "unavailable",
            "reason": MISSING_REASONS[protocol],
        }
        for protocol in PROTOCOLS
        if protocol not in fixtures
    }
    if not fixtures:
        pytest.skip(
            "all protocol fixtures unavailable: " + json.dumps(statuses, sort_keys=True)
        )

    original = None
    restored = False
    failures: list[str] = []
    async with session() as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {
            "dependency_doctor",
            "get_capabilities",
            "hardware_preflight",
            "backup_flash",
            "deploy_protocol_bridge",
            "get_protocol_bridge_status",
            "restore_flash_backup",
            "reset_target",
            *TOOLS,
        } <= names
        doctor = unpack(await client.call_tool("dependency_doctor", {}))
        required_failures = [
            item["name"]
            for item in doctor["checks"]
            if item["required"] and not item["ok"]
        ]
        assert not required_failures, required_failures
        capabilities = unpack(await client.call_tool("get_capabilities", {}))
        assert capabilities["workflows"]["protocol_bridge_deploy"] == "available"
        preflight = unpack(
            await client.call_tool("hardware_preflight", {"selector": selector})
        )
        assert preflight["ok"]
        backup_result = unpack(
            await client.call_tool(
                "backup_flash",
                {"address": 0x08000000, "size": 0x200000, "selector": selector},
            )
        )
        original = backup_result["artifact"]
        assert original["size"] == 0x200000 and len(original["sha256"]) == 64

        try:
            deployed = unpack(
                await client.call_tool("deploy_protocol_bridge", {"selector": selector})
            )
            assert deployed["flash"]["ok"]
            assert deployed["handshake"]["wire_version"] == 1
            status = unpack(
                await client.call_tool(
                    "get_protocol_bridge_status", {"selector": selector}
                )
            )
            assert status["wire_version"] == 1
            for protocol, case in fixtures.items():
                try:
                    for action in case["actions"]:
                        tool = action.get("tool")
                        assert tool in TOOLS, f"unsupported HIL action tool: {tool}"
                        result = unpack(
                            await client.call_tool(
                                tool,
                                {
                                    "request": action["request"],
                                    "selector": selector,
                                },
                            )
                        )
                        _check_expectation(protocol, result, action.get("expect", {}))
                    statuses[protocol] = {
                        "state": "available",
                        "reason": "configured physical fixture passed every declared action",
                    }
                except Exception as exc:  # noqa: BLE001 -- restoration precedes failure
                    statuses[protocol] = {
                        "state": "failed",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                    failures.append(protocol)
        finally:
            if original:
                restore = unpack(
                    await client.call_tool(
                        "restore_flash_backup",
                        {
                            "backup_path": original["path"],
                            "address": 0x08000000,
                            "expected_sha256": original["sha256"],
                            "selector": selector,
                        },
                    )
                )
                restored = restore["ok"]
        assert restored
        restored_backup = unpack(
            await client.call_tool(
                "backup_flash",
                {"address": 0x08000000, "size": 0x200000, "selector": selector},
            )
        )["artifact"]
        assert restored_backup["sha256"] == original["sha256"]
        assert unpack(
            await client.call_tool(
                "reset_target", {"selector": selector, "halt": False}
            )
        )["ok"]

    with capsys.disabled():
        print("PROTOCOL_BRIDGE_HIL " + json.dumps(statuses, sort_keys=True))
    assert not failures, f"protocol fixtures failed: {failures}; statuses={statuses}"
