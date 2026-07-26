from __future__ import annotations

import shutil
from pathlib import Path

from jlink_mcp_arduino_giga import artifacts
from jlink_mcp_arduino_giga.artifacts import finalize_fixture_elf, verify_fixture_elf

from jlink_mcp.artifacts import inspect_elf


def _build_synthetic_fixture(tmp_path: Path) -> Path:
    elf = tmp_path / "fixture.elf"
    fixture = Path(__file__).parent / "fixtures/synthetic_fixture.elf"
    shutil.copyfile(fixture, elf)
    return elf


def test_fixture_finalize_verify_and_corruption(tmp_path: Path) -> None:
    elf = _build_synthetic_fixture(tmp_path)
    assert "jlink_mcp_manifest" in inspect_elf(elf)["test_symbols"]
    finalized = finalize_fixture_elf(elf)
    assert finalized["flash_start"] == "0x08000000"
    assert finalized["ram_start"] == "0x24000000"
    assert verify_fixture_elf(elf)["ok"]
    blob = bytearray(elf.read_bytes())
    offset, _, _ = artifacts._manifest_location(blob)
    blob[offset + 140] ^= 0x01
    elf.write_bytes(blob)
    assert not verify_fixture_elf(elf)["ok"]
