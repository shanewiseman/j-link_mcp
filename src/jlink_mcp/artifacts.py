"""Target-neutral artifact hashing and ELF inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from elftools.elf.elffile import ELFFile

from .models import Artifact
from .store import sha256_file


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
