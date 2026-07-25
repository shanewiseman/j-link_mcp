from __future__ import annotations

import pytest

from .conftest import HIL_ENABLED, session, unpack


pytestmark = [
    pytest.mark.hil,
    pytest.mark.hardware,
    pytest.mark.destructive,
    pytest.mark.skipif(not HIL_ENABLED, reason="set JLINK_MCP_HIL=1"),
]


def _artifact(build: dict, kind: str) -> dict:
    return next(item for item in build["artifacts"] if item["kind"] == kind)


def _memory_map(snapshot: dict) -> dict[int, int]:
    return {
        int(item["address"], 0): int(item["values"][0], 0)
        for item in snapshot["parsed"]["memory"]
    }


def _persistent_registers(snapshot: dict) -> dict[int, int]:
    values = _memory_map(snapshot)
    for address in (0x5200201C, 0x5200211C):
        if address in values:
            values[address] &= ~0x00400000  # transient BCM4 current-status bit
    return values


@pytest.mark.asyncio
async def test_complete_giga_acceptance_and_restore(selector) -> None:
    """Execute the plan's destructive sequence exclusively through MCP calls."""

    original = None
    restored = False
    async with session() as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {
            "hardware_preflight",
            "prepare_giga_dual_core_debug",
            "backup_flash",
            "deploy_dual_core_firmware",
            "boot_and_observe",
            "assert_debug_fixture",
            "capture_controlled_crash",
            "capture_rtt",
            "swo_control",
            "restore_flash_backup",
        } <= names

        preflight = unpack(
            await client.call_tool(
                "hardware_preflight",
                {"selector": selector, "prepare_dual_core": True},
            )
        )
        assert preflight["ok"]
        assert preflight["preparation"]["ok"]
        assert preflight["m7_identity"]["target_identity"]["cpuid"].lower() == "0x411fc271"
        assert preflight["m4_identity"]["target_identity"]["cpuid"].lower() == "0x410fc241"
        assert preflight["register_snapshot"]["ok"]

        backup = unpack(
            await client.call_tool(
                "backup_flash",
                {"address": 0x08000000, "size": 0x200000, "selector": selector},
            )
        )
        original = backup["artifact"]
        assert original and original["size"] == 0x200000
        assert len(original["sha256"]) == 64

        try:
            deployed = unpack(
                await client.call_tool(
                    "deploy_dual_core_firmware",
                    {
                        "selector": selector,
                        "m7_sketch": "firmware/giga_hil/m7",
                        "m4_sketch": "firmware/giga_hil/m4",
                        "flash_split": "75_25",
                    },
                )
            )
            assert deployed["ok"]
            m7 = deployed["m7_build"]
            m4 = deployed["m4_build"]
            for build in (m7, m4):
                assert build["command"]["return_code"] == 0
                assert {"elf", "bin", "hex", "manifest", "checksums"} <= {
                    item["kind"] for item in build["artifacts"]
                }
                assert len(build["artifacts"][0]["sha256"]) == 64
            assert deployed["m7_manifest"]["verification"]["ok"]
            assert deployed["m4_manifest"]["verification"]["ok"]
            assert len(deployed["m7_build_identity"]["git_commit"]) == 40
            assert len(deployed["m4_build_identity"]["git_commit"]) == 40

            preserved = unpack(
                await client.call_tool(
                    "compare_backup_region",
                    {
                        "backup_path": original["path"],
                        "backup_offset": 0,
                        "address": 0x08000000,
                        "size": 0x40000,
                        "selector": selector,
                    },
                )
            )
            assert preserved["match"]
            post_flash_preflight = unpack(
                await client.call_tool("hardware_preflight", {"selector": selector})
            )
            assert _persistent_registers(
                post_flash_preflight["register_snapshot"]
            ) == _persistent_registers(preflight["register_snapshot"])

            m7_elf = _artifact(m7, "elf")["path"]
            m4_elf = _artifact(m4, "elf")["path"]
            observed = unpack(
                await client.call_tool(
                    "boot_and_observe",
                    {
                        "selector": selector,
                        "m7_elf_path": m7_elf,
                        "m4_elf_path": m4_elf,
                    },
                )
            )
            assert observed["ok"]

            debug = unpack(
                await client.call_tool(
                    "assert_debug_fixture", {"elf_path": m7_elf, "selector": selector}
                )
            )
            assert debug["ok"]

            rtt = unpack(
                await client.call_tool(
                    "capture_rtt",
                    {"elf_path": m7_elf, "selector": selector, "duration_seconds": 2.0},
                )
            )
            assert rtt["ok"] and rtt["artifact"]["size"] > 0
            assert '"fixture":"JLINK_MCP_HIL"' in rtt["text"]
            assert '"core":"m7"' in rtt["text"]

            swo = unpack(
                await client.call_tool(
                    "swo_control",
                    {
                        "action": "capture",
                        "speed_hz": 2_000_000,
                        "capture_ms": 250,
                        "selector": selector,
                    },
                )
            )
            # SWO data requires the optional physical wire; command success and
            # capability reporting are mandatory even when the stream is empty.
            if not swo["ok"]:
                capabilities = unpack(await client.call_tool("get_capabilities", {}))
                assert capabilities["features"]["swo_wire"]["reason"]

            crash = unpack(
                await client.call_tool(
                    "capture_controlled_crash",
                    {"elf_path": m7_elf, "selector": selector},
                )
            )
            assert crash["ok"]

            # Prove address-bearing external formats plus raw BIN handling.
            for kind in ("elf", "hex"):
                result = unpack(
                    await client.call_tool(
                        "flash_and_verify",
                        {"artifact_path": _artifact(m7, kind)["path"], "selector": selector},
                    )
                )
                assert result["return_code"] == 0
            address = int(deployed["m7_manifest"]["flash_start"], 0)
            m7_bin = _artifact(m7, "bin")
            result = unpack(
                await client.call_tool(
                    "flash_binary",
                    {"artifact_path": m7_bin["path"], "address": address, "selector": selector},
                )
            )
            assert result["return_code"] == 0
            compared = unpack(
                await client.call_tool(
                    "compare_firmware",
                    {"artifact_path": m7_bin["path"], "address": address, "selector": selector},
                )
            )
            assert compared["match"]

            assert unpack(
                await client.call_tool("disconnect_target", {"selector": selector})
            )["parsed"]["disconnected"]
            assert unpack(
                await client.call_tool("connect_target", {"selector": selector})
            )["ok"]
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
        reset = unpack(
            await client.call_tool(
                "reset_target", {"selector": selector, "halt": False}
            )
        )
        assert reset["ok"]
        assert unpack(
            await client.call_tool("connect_target", {"selector": selector})
        )["ok"]
        report = unpack(
            await client.call_tool(
                "generate_validation_report", {"title": "GIGA HIL acceptance"}
            )
        )
        assert report["audit_chain_ok"]
