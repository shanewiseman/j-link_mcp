from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from jlink_mcp.config import Settings
from jlink_mcp.server import MCPRuntime


SCHEMA_HASHES = {
    "assert_debug_fixture": "ecfa5d3111b76a83458a7ff2af5352766a6fab0dfbff4967aa964f4ba07f90d4",
    "boot_and_observe": "c3c1a2900e03c653a7f4627f7b67ae1a2687e07f2c63eccdc302999bd85d04fc",
    "build_giga_firmware": "0395e6332bf9aa8d7380569d5ff81a332068a70cb8321eb09cbad5b407a2ca36",
    "build_protocol_bridge_release": "9a7869461f93db5ac58b4054fa2a16634f2733fa3a3808f9aba9801915e1f18d",
    "capture_controlled_crash": "9367c8c2ed1c111b843a787a50f613d3f0af926cebab64f3452fdfc91ad07fef",
    "deploy_dual_core_firmware": "c1604c3586af4c54f6e2338e4ba827547f9faf399dcebacd5efd039c3eb01b92",
    "deploy_protocol_bridge": "711575d43f26be73bb94118cc4da261f3d8192472f1da37ab551280c34a842bb",
    "get_protocol_bridge_status": "416afe4dfd4a3208b930d46588bdf97dbadf3cedcc5b7d875f37fcca314ab9f6",
    "hardware_preflight": "1e5fdd703f6d8889690af9e6b1be64cad99f2879d171a0987dc60ea9424e8090",
    "prepare_giga_dual_core_debug": "2b6135395c30330f66dd491d53dc1b92c19f68b6855872f0a3af5dfffe4f935b",
    "protocol_bridge_control": "b5237f12102c759dd119a77d5c1f41202ff0e0ea1c865a8ce441b288fb1400d7",
    "protocol_bridge_exchange": "08f58f569522cbcd7bca0be4afd0b93b6c821f0a7ca412950bc939889bac9a3a",
    "protocol_bridge_receive": "5d59e0c2d09296f690c27af50c725d966bf2ad54b72e7ca39255fc327f4c31f6",
    "validate_giga_fixture": "9aff803d0dc9a534b518465d7dd178aa2caad3083771bdf0055e3d79a0920f17",
}


def test_combined_first_party_bundle_preserves_tool_schemas(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    runtime = MCPRuntime(
        Settings(
            repository_root=root,
            workspace_root=root,
            state_root=root / "state",
            segger_root=root,
            token="test",
            extensions=["arduino_giga", "giga_protocol_bridge"],
        )
    )
    try:
        assert runtime.extensions.loaded_ids == [
            "arduino_giga",
            "giga_protocol_bridge",
        ]
        tools = asyncio.run(runtime.mcp.list_tools())
        observed = {}
        for tool in tools:
            if tool.name not in SCHEMA_HASHES:
                continue
            canonical = json.dumps(
                {"input": tool.inputSchema, "output": tool.outputSchema},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            observed[tool.name] = hashlib.sha256(canonical).hexdigest()
        assert observed == SCHEMA_HASHES
        manifest = runtime.service.capabilities()
        assert [item.id for item in manifest.extensions] == [
            "arduino_giga",
            "giga_protocol_bridge",
        ]
        assert manifest.extensions[1].dependencies == ["arduino_giga"]
    finally:
        asyncio.run(runtime.extensions.shutdown())
        asyncio.run(runtime.service.close())


def test_core_distribution_surfaces_are_hardware_neutral() -> None:
    repository = Path(__file__).resolve().parents[1]
    forbidden = ("arduino", "giga", "stm32h747", "protocol_bridge", "fqbn")
    implementation = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted((repository / "src/jlink_mcp").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    ).lower()
    assert not [term for term in forbidden if term in implementation]
    for relative in (
        "Dockerfile",
        "compose.yaml",
        "sbom/jlink-mcp.cdx.json",
        "sbom/python-licenses.md",
    ):
        text = (repository / relative).read_text(encoding="utf-8").lower()
        assert not [term for term in forbidden if term in text], relative
