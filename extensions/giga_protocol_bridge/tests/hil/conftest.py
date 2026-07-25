from __future__ import annotations

import os

import pytest


@pytest.fixture
def selector() -> dict[str, object]:
    return {
        "probe_serial": os.environ.get("JLINK_MCP_PROBE_SERIAL", "000802008248"),
        "board_serial": os.environ.get(
            "JLINK_MCP_BOARD_SERIAL", "0045002B3333511632363530"
        ),
        "target_profile": "arduino_giga_r1",
        "core": "m7",
        "interface": "SWD",
        "speed_khz": 4000,
    }
