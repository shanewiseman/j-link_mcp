"""Artifact hashing and ELF inspection."""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path
from typing import Any

from elftools.elf.elffile import ELFFile

from jlink_mcp.models import Artifact
from jlink_mcp.store import sha256_file

# Versioned layout of JLinkMCPManifest in the fixture headers. Static assertions
# in those headers make a compiler/layout drift fail at build time.
_MANIFEST_SIZE_OFFSET = 136
_MANIFEST_CRC_OFFSET = 140
_MANIFEST_FLASH_START_OFFSET = 144
_MANIFEST_FLASH_SIZE_OFFSET = 148
_MANIFEST_RAM_START_OFFSET = 152
_MANIFEST_RAM_SIZE_OFFSET = 156


def registerable_artifact(path: Path, *, kind: str) -> Artifact:
    return Artifact.from_path(path, kind=kind, sha256=sha256_file(path))


def inspect_elf(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        sections = [
            {
                "name": section.name,
                "address": section["sh_addr"],
                "size": section["sh_size"],
                "type": section["sh_type"],
                "flags": section["sh_flags"],
            }
            for section in elf.iter_sections()
            if section["sh_size"]
        ]
        symbols: dict[str, dict[str, int]] = {}
        symbol_table = elf.get_section_by_name(".symtab")
        if symbol_table:
            for symbol in symbol_table.iter_symbols():
                if symbol.name.startswith("jlink_mcp_") or symbol.name == "_SEGGER_RTT":
                    symbols[symbol.name] = {
                        "address": symbol["st_value"],
                        "size": symbol["st_size"],
                    }
        segments = [
            {
                "type": segment["p_type"],
                "virtual_address": segment["p_vaddr"],
                "physical_address": segment["p_paddr"],
                "file_size": segment["p_filesz"],
                "memory_size": segment["p_memsz"],
                "flags": segment["p_flags"],
            }
            for segment in elf.iter_segments()
        ]
        return {
            "elf_class": elf.elfclass,
            "little_endian": elf.little_endian,
            "machine": elf["e_machine"],
            "entry": elf["e_entry"],
            "sections": sections,
            "segments": segments,
            "test_symbols": symbols,
        }


def finalize_fixture_elf(path: Path) -> dict[str, int | str]:
    """Patch the fixture's embedded size, CRC, flash, and RAM layout.

    The CRC is IEEE CRC-32 over the contiguous flash load image with the CRC
    field treated as zero. Gaps between loadable flash segments are 0xFF. This
    avoids a self-referential checksum while remaining reproducible from the
    final ELF.
    """

    blob = bytearray(path.read_bytes())
    manifest_file_offset, manifest_address, symbol_size = _manifest_location(blob)
    if symbol_size < _MANIFEST_RAM_SIZE_OFFSET + 4:
        raise ValueError("fixture manifest is older than the supported layout")

    flash_start, flash_image, ram_start, ram_size = _layout_from_blob(blob)
    image_size = len(flash_image)
    for offset, value in (
        (_MANIFEST_SIZE_OFFSET, image_size),
        (_MANIFEST_CRC_OFFSET, 0),
        (_MANIFEST_FLASH_START_OFFSET, flash_start),
        (_MANIFEST_FLASH_SIZE_OFFSET, image_size),
        (_MANIFEST_RAM_START_OFFSET, ram_start),
        (_MANIFEST_RAM_SIZE_OFFSET, ram_size),
    ):
        struct.pack_into("<I", blob, manifest_file_offset + offset, value)

    # Re-extract after layout fields have been patched. The CRC field remains
    # zero for the checksum calculation.
    _, flash_image, _, _ = _layout_from_blob(blob)
    crc32 = zlib.crc32(flash_image) & 0xFFFFFFFF
    struct.pack_into(
        "<I", blob, manifest_file_offset + _MANIFEST_CRC_OFFSET, crc32
    )
    path.write_bytes(blob)
    return {
        "manifest_address": f"0x{manifest_address:08X}",
        "image_size": image_size,
        "image_crc32": f"0x{crc32:08X}",
        "crc_definition": "IEEE CRC-32 over flash image with CRC field zeroed",
        "flash_start": f"0x{flash_start:08X}",
        "flash_size": image_size,
        "ram_start": f"0x{ram_start:08X}",
        "ram_size": ram_size,
    }


def verify_fixture_elf(path: Path) -> dict[str, int | str | bool]:
    """Recompute and verify a finalized embedded fixture manifest."""

    blob = bytearray(path.read_bytes())
    manifest_file_offset, manifest_address, _ = _manifest_location(blob)
    expected_size = struct.unpack_from(
        "<I", blob, manifest_file_offset + _MANIFEST_SIZE_OFFSET
    )[0]
    expected_crc = struct.unpack_from(
        "<I", blob, manifest_file_offset + _MANIFEST_CRC_OFFSET
    )[0]
    struct.pack_into("<I", blob, manifest_file_offset + _MANIFEST_CRC_OFFSET, 0)
    flash_start, flash_image, _, _ = _layout_from_blob(blob)
    actual_crc = zlib.crc32(flash_image) & 0xFFFFFFFF
    return {
        "ok": expected_size == len(flash_image) and expected_crc == actual_crc,
        "manifest_address": f"0x{manifest_address:08X}",
        "flash_start": f"0x{flash_start:08X}",
        "expected_size": expected_size,
        "actual_size": len(flash_image),
        "expected_crc32": f"0x{expected_crc:08X}",
        "actual_crc32": f"0x{actual_crc:08X}",
    }


def _manifest_location(blob: bytes | bytearray) -> tuple[int, int, int]:
    with io.BytesIO(blob) as handle:
        elf = ELFFile(handle)
        table = elf.get_section_by_name(".symtab")
        if table is None:
            raise ValueError("ELF has no symbol table")
        symbol = next(
            (item for item in table.iter_symbols() if item.name == "jlink_mcp_manifest"),
            None,
        )
        if symbol is None or not isinstance(symbol["st_shndx"], int):
            raise ValueError("ELF has no concrete jlink_mcp_manifest symbol")
        section = elf.get_section(symbol["st_shndx"])
        offset = int(section["sh_offset"]) + int(symbol["st_value"]) - int(
            section["sh_addr"]
        )
        return offset, int(symbol["st_value"]), int(symbol["st_size"])


def _layout_from_blob(blob: bytes | bytearray) -> tuple[int, bytes, int, int]:
    with io.BytesIO(blob) as handle:
        elf = ELFFile(handle)
        segments: list[tuple[int, bytes]] = []
        for segment in elf.iter_segments():
            address = int(segment["p_paddr"])
            size = int(segment["p_filesz"])
            if segment["p_type"] == "PT_LOAD" and size and 0x08000000 <= address < 0x10000000:
                segments.append((address, segment.data()))
        if not segments:
            raise ValueError("ELF has no loadable STM32 flash segments")
        start = min(address for address, _ in segments)
        end = max(address + len(data) for address, data in segments)
        image = bytearray(b"\xFF" * (end - start))
        for address, data in segments:
            image[address - start : address - start + len(data)] = data

        symbol_table = elf.get_section_by_name(".symtab")
        test_buffer_address = None
        if symbol_table is not None:
            test_buffer = next(
                (
                    symbol
                    for symbol in symbol_table.iter_symbols()
                    if symbol.name == "jlink_mcp_test_buffer"
                ),
                None,
            )
            if test_buffer is not None:
                test_buffer_address = int(test_buffer["st_value"])

        ram_sections = [
            section
            for section in elf.iter_sections()
            if int(section["sh_size"])
            and int(section["sh_flags"]) & 0x2
            and int(section["sh_flags"]) & 0x1
            and 0x10000000 <= int(section["sh_addr"]) < 0x70000000
        ]
        if test_buffer_address is not None:
            # STM32H747 RAM is split across several non-contiguous banks. The
            # manifest describes the bank that owns the deterministic fixture
            # buffer, rather than falsely spanning holes between all banks.
            bank = test_buffer_address >> 24
            ram_sections = [
                section
                for section in ram_sections
                if int(section["sh_addr"]) >> 24 == bank
            ]
        if ram_sections:
            ram_start = min(int(section["sh_addr"]) for section in ram_sections)
            ram_end = max(
                int(section["sh_addr"]) + int(section["sh_size"])
                for section in ram_sections
            )
        else:
            ram_start = 0
            ram_end = 0
        return start, bytes(image), ram_start, ram_end - ram_start
