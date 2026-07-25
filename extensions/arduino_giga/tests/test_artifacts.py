from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jlink_mcp.artifacts import inspect_elf
from jlink_mcp_arduino_giga import artifacts
from jlink_mcp_arduino_giga.artifacts import finalize_fixture_elf, verify_fixture_elf


def _build_synthetic_fixture(tmp_path: Path) -> Path:
    cc = shutil.which("cc")
    ld = shutil.which("ld")
    if not cc or not ld:
        pytest.skip("native compiler/linker unavailable")
    source = tmp_path / "fixture.c"
    source.write_text(
        '__attribute__((section(".manifest"))) unsigned char jlink_mcp_manifest[200];\n'
        '__attribute__((section(".ram"))) unsigned char jlink_mcp_test_buffer[32];\n'
        '__attribute__((section(".rtt"))) unsigned char _SEGGER_RTT[64];\n'
        'void jlink_mcp_breakpoint_site(void) {}\n',
        encoding="utf-8",
    )
    script = tmp_path / "fixture.ld"
    script.write_text(
        "SECTIONS { . = 0x08000000; .text : { *(.text*) } "
        ".manifest : { *(.manifest) } . = 0x24000000; "
        ".ram : { *(.ram) *(.rtt) } }\n",
        encoding="utf-8",
    )
    obj = tmp_path / "fixture.o"
    elf = tmp_path / "fixture.elf"
    subprocess.run([cc, "-c", str(source), "-o", str(obj)], check=True)
    subprocess.run([ld, "-T", str(script), str(obj), "-o", str(elf)], check=True)
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
