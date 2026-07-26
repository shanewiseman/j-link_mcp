#!/usr/bin/env python3
"""Non-destructively prove an attached J-Link and Arduino GIGA R1 work via MCP."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED = {
    "dpidr": "0x6ba02477",
    "m7": "0x411fc271",
    "m4": "0x410fc241",
}


class DemoError(RuntimeError):
    pass


def unpack(result: Any) -> Any:
    if result.isError:
        detail = " ".join(
            text for item in result.content if (text := getattr(item, "text", None))
        )
        raise DemoError(detail or "MCP tool call failed")
    if result.structuredContent is not None:
        value = result.structuredContent
        return value.get("result", value) if isinstance(value, dict) else value
    for item in result.content:
        if text := getattr(item, "text", None):
            return json.loads(text)
    raise DemoError("MCP result contained no JSON")


def choose_serial(requested: str | None, discovered: str | None, kind: str) -> str:
    serial = requested or discovered
    if not serial:
        raise DemoError(
            f"{kind} selection is ambiguous; pass --{kind}-serial explicitly"
        )
    return serial


def summarize_core(result: dict[str, Any], core: str) -> dict[str, Any]:
    if not result.get("ok"):
        raise DemoError(f"{core.upper()} connection failed")
    identity = result.get("target_identity", {})
    cpuid = str(identity.get("cpuid", "")).lower()
    dpidr = str(identity.get("dpidr", "")).lower()
    voltage = identity.get("target_voltage")
    if cpuid != EXPECTED[core]:
        raise DemoError(f"{core.upper()} CPUID {cpuid!r} != expected {EXPECTED[core]}")
    if dpidr != EXPECTED["dpidr"]:
        raise DemoError(
            f"{core.upper()} DPIDR {dpidr!r} != expected {EXPECTED['dpidr']}"
        )
    if not isinstance(voltage, (int, float)) or voltage < 1.0:
        raise DemoError(f"{core.upper()} target voltage is unsafe: {voltage!r}")
    return {
        "operation_id": result.get("operation_id"),
        "observed_core": identity.get("observed_core"),
        "cpuid": cpuid,
        "dpidr": dpidr,
        "target_voltage": voltage,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    token = args.token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise DemoError(f"empty token file: {args.token_file}")

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as http:
        health = await http.get(args.url.removesuffix("/mcp") + "/healthz")
        health.raise_for_status()

        async with (
            streamable_http_client(args.url, http_client=http) as (read, write, _),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            inventory = await client.list_tools()
            required = {
                "dependency_doctor",
                "get_capabilities",
                "list_jlink_probes",
                "hardware_preflight",
            }
            missing = required - {tool.name for tool in inventory.tools}
            if missing:
                raise DemoError("missing MCP tools: " + ", ".join(sorted(missing)))

            # Required by the repository contract before target operations.
            doctor = unpack(await client.call_tool("dependency_doctor", {}))
            failed = [
                check["name"]
                for check in doctor["checks"]
                if check["required"] and not check["ok"]
            ]
            if failed:
                raise DemoError(
                    "required dependency checks failed: " + ", ".join(failed)
                )
            capabilities = unpack(await client.call_tool("get_capabilities", {}))

            probe_serial = choose_serial(
                args.probe_serial,
                capabilities.get("selected_probe_serial"),
                "probe",
            )
            board_serial = choose_serial(
                args.board_serial,
                capabilities.get("selected_board_serial"),
                "board",
            )
            probes = {item["serial"]: item for item in capabilities.get("probes", [])}
            boards = {
                item["serial"]: item
                for item in capabilities.get("boards", [])
                if item.get("serial")
            }
            if probe_serial not in probes:
                raise DemoError(f"J-Link is not attached: {probe_serial}")
            if board_serial not in boards:
                raise DemoError(f"GIGA is not attached: {board_serial}")
            board = boards[board_serial]
            if board.get("target_profile") != "arduino_giga_r1":
                raise DemoError(f"board is not a GIGA R1: {board_serial}")

            enumeration = unpack(await client.call_tool("list_jlink_probes", {}))
            if not enumeration.get("ok"):
                raise DemoError("J-Link Commander enumeration failed")

            selector = {
                "probe_serial": probe_serial,
                "board_serial": board_serial,
                "target_profile": "arduino_giga_r1",
                "core": "m7",
                "interface": "SWD",
                "speed_khz": 4000,
            }
            preflight = unpack(
                await client.call_tool(
                    "hardware_preflight",
                    {
                        "selector": selector,
                        "prepare_dual_core": True,
                    },
                )
            )
            if not preflight.get("ok"):
                raise DemoError("GIGA hardware preflight failed")
            snapshot = preflight["register_snapshot"]
            if not snapshot.get("ok"):
                raise DemoError("GIGA register snapshot failed")

            return {
                "status": "PASS",
                "non_destructive": True,
                "mcp_health": health.json(),
                "dependency_doctor": "all required checks passed",
                "transient_dual_core_preparation": {
                    "requested": True,
                    "rcc_gcr_changed": preflight["preparation"]["changed"],
                    "persistent_configuration_changed": False,
                },
                "jlink": {
                    "serial": probe_serial,
                    "model": probes[probe_serial]["model"],
                    "enumeration_operation_id": enumeration.get("operation_id"),
                },
                "giga": {
                    "serial": board_serial,
                    "model": board["model"],
                    "mcu": board["mcu"],
                    "target_profile": board["target_profile"],
                },
                "m7": summarize_core(preflight["m7_identity"], "m7"),
                "m4": summarize_core(preflight["m4_identity"], "m4"),
                "register_snapshot_operation_id": snapshot.get("operation_id"),
            }


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/mcp",
        help="J-Link MCP Streamable HTTP endpoint",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path(".token"),
        help="MCP bearer-token file",
    )
    parser.add_argument("--probe-serial", help="stable J-Link serial override")
    parser.add_argument("--board-serial", help="stable Arduino serial override")
    return parser.parse_args()


def main() -> int:
    try:
        evidence = asyncio.run(run(arguments()))
    except (DemoError, OSError, httpx.HTTPError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
