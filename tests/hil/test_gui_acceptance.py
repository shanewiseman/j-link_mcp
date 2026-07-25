from __future__ import annotations

import anyio
import pytest

from .conftest import GUI_ENABLED, session, unpack


pytestmark = [
    pytest.mark.gui,
    pytest.mark.hardware,
    pytest.mark.skipif(not GUI_ENABLED, reason="set JLINK_MCP_GUI=1"),
]


@pytest.mark.asyncio
async def test_installed_segger_gui_accessibility_screenshot_and_ocr(selector) -> None:
    async with session() as client:
        capabilities = unpack(await client.call_tool("get_capabilities", {}))
        installed = {
            item["name"]
            for item in capabilities["tools"]
            if item["state"] == "available"
        }
        gui_allowlist = {
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
        candidates = sorted(
            name
            for name in gui_allowlist
            if name in installed
        )
        assert candidates
        for application in candidates:
            launched = unpack(
                await client.call_tool(
                    "launch_segger_gui",
                    {"application": application, "args": [], "selector": selector},
                )
            )
            session_id = launched["session_id"]
            try:
                await anyio.sleep(0.5)
                info = unpack(
                    await client.call_tool(
                        "gui_session_info", {"session_id": session_id}
                    )
                )
                assert info["application"] == application
                assert info["running"] is True
                tree = unpack(
                    await client.call_tool(
                        "gui_accessibility_tree", {"session_id": session_id}
                    )
                )
                assert tree["return_code"] == 0
                assert tree["stdout"].strip().startswith("{")
                keys = unpack(
                    await client.call_tool(
                        "gui_keys", {"session_id": session_id, "keys": "Tab"}
                    )
                )
                assert keys["return_code"] == 0
                screenshot = unpack(
                    await client.call_tool(
                        "gui_screenshot", {"session_id": session_id}
                    )
                )
                assert screenshot["evidence_paths"]
                ocr = unpack(
                    await client.call_tool(
                        "gui_ocr", {"screenshot_path": screenshot["evidence_paths"][-1]}
                    )
                )
                assert ocr["return_code"] == 0
            finally:
                stopped = unpack(
                    await client.call_tool(
                        "stop_segger_gui", {"session_id": session_id}
                    )
                )
                assert stopped["stopped"]
