from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from elftools.common.exceptions import ELFError

from jlink_mcp import discovery, doctor
from jlink_mcp.artifacts import inspect_elf, registerable_artifact
from jlink_mcp.models import CapabilityState
from jlink_mcp.server import CORE_TOOL_NAMES, BearerTokenASGI, MCPRuntime


class Attributes:
    def __init__(self, values):
        self.values = values

    def get(self, name):
        value = self.values.get(name)
        if isinstance(value, Exception):
            raise value
        return value


class Device:
    def __init__(self, properties, attributes, *, node=None, children=()):
        self.properties = properties
        self.attributes = Attributes(attributes)
        self.device_node = node
        self.children = list(children)
        self.sys_path = "/sys/fake/" + (properties.get("DEVNUM") or "0")


def test_usb_discovery_udev_and_sysfs_fallback(monkeypatch) -> None:
    tty = SimpleNamespace(device_node="/dev/ttyACM7")
    jlink = Device(
        {"ID_VENDOR_ID": "1366", "ID_MODEL_ID": "1020", "ID_SERIAL_SHORT": "J1"},
        {},
        node="/dev/bus/usb/001/002",
    )
    target = Device(
        {"BUSNUM": "001", "DEVNUM": "003"},
        {
            "idVendor": b"2341\n",
            "idProduct": b"0266\n",
            "serial": b"G1\n",
            "manufacturer": b"Example\n",
            "product": b"Sample target\n",
        },
        children=[tty],
    )
    ignored = Device({}, {"idVendor": b"1234", "idProduct": b"0001"})
    context = SimpleNamespace(list_devices=lambda **kwargs: [jlink, target, ignored])
    monkeypatch.setattr(discovery.pyudev, "Context", lambda: context)
    found = discovery.discover_usb_devices()
    assert [item.kind for item in found] == ["jlink", "usb", "usb"]
    assert found[1].serial == "G1"
    assert found[1].device_nodes == ["/dev/ttyACM7"]
    assert discovery._clean_hex("0XAB") == "00ab"
    broken = Device({}, {"serial": OSError("gone")})
    assert discovery._attribute(broken, "serial") is None


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_tool_and_capability_discovery(
    settings, monkeypatch, manifest, target_registry
) -> None:
    settings.segger_root = settings.segger_root.parent / "JLink_V962"
    settings.segger_root.mkdir()
    for name in ("JLinkExe", "JLinkGDBServerCLExe", "JLinkRTTLoggerExe"):
        _executable(settings.segger_root / name)
    gdb = _executable(settings.workspace_root / "bin" / "gdb-client")
    settings.gdb_client = str(gdb)
    monkeypatch.setattr(
        discovery.shutil,
        "which",
        lambda name: (
            f"/usr/bin/{name}"
            if name in {"Xvfb", "xdotool", "tesseract", "scrot"}
            else None
        ),
    )
    usb = [manifest.probes[0].usb, manifest.boards[0].usb]
    monkeypatch.setattr(discovery, "discover_usb_devices", lambda: usb)
    tools = discovery.discover_tools(settings)
    lookup = {item.name: item for item in tools}
    assert lookup["JLinkExe"].version == "9.62"
    assert lookup["JFlashExe"].state == CapabilityState.UNAVAILABLE
    assert lookup["gdb-client"].state == CapabilityState.AVAILABLE
    target_registry.register_board_detector(
        "sample-usb",
        lambda usb: (
            manifest.boards[0] if usb.serial == manifest.boards[0].serial else None
        ),
    )
    capability = discovery.capability_manifest(settings, target_registry)
    assert capability.unique_pair
    assert capability.selected_probe_serial == "000802008248"
    assert capability.workflows["flash_verify"] == CapabilityState.AVAILABLE
    assert capability.workflows["debug"] == CapabilityState.AVAILABLE
    assert capability.features["target_power"].state == CapabilityState.UNAVAILABLE
    assert capability.workflow_details["sdk"].reason
    assert any("educational" in item for item in capability.limitations)


def test_segger_version_release_notes_and_groups(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "SEGGER"
    notes = root / "Doc" / "ReleaseNotes" / "ReleaseNotes.html"
    notes.parent.mkdir(parents=True)
    notes.write_text("<h1>Version V9.62a</h1>", encoding="utf-8")
    assert discovery._segger_version(root) == "9.62a"
    assert discovery._segger_version(tmp_path / "none") is None
    monkeypatch.setattr(discovery.os, "getgroups", lambda: [123])
    monkeypatch.setattr(
        discovery.grp, "getgrgid", lambda gid: SimpleNamespace(gr_name="plugdev")
    )
    assert discovery.current_groups() == {"plugdev"}


def test_current_groups_preserves_unmapped_container_gid(monkeypatch) -> None:
    monkeypatch.setattr(discovery.os, "getgroups", lambda: [1000])
    monkeypatch.setattr(
        discovery.grp,
        "getgrgid",
        lambda gid: (_ for _ in ()).throw(KeyError(gid)),
    )
    assert discovery.current_groups() == {"gid:1000"}


def test_dependency_doctor_full_matrix(
    settings, manifest, monkeypatch, tmp_path: Path, target_registry
) -> None:
    # Make all synthetic nodes and target-neutral prerequisites observable.
    probe_node = tmp_path / "jlink-node"
    board_node = tmp_path / "ttyACM0"
    for node in (probe_node, board_node):
        node.touch()
        node.chmod(0o660)
    manifest.probes[0].usb.device_nodes = [str(probe_node)]
    manifest.boards[0].usb.device_nodes = [str(board_node)]
    manifest.boards[0].serial_port = str(board_node)
    for tool in ("JLinkExe", "JLinkGDBServerCLExe", "gdb-client", "Xvfb", "xdotool"):
        from jlink_mcp.models import ToolAvailability

        manifest.tools.append(
            ToolAvailability(
                name=tool,
                state=CapabilityState.AVAILABLE,
                path=f"/fake/{tool}",
                version="9.62" if tool == "JLinkExe" else None,
            )
        )
    monkeypatch.setattr(
        doctor, "capability_manifest", lambda settings, targets=None: manifest
    )
    monkeypatch.setattr(doctor, "current_groups", lambda: {"plugdev", "dialout"})
    monkeypatch.setattr(doctor, "_filesystem_type", lambda path: "cgroup2fs")
    monkeypatch.setattr(doctor, "_command_ok", lambda argv: True)
    monkeypatch.setattr(doctor, "_command_output", lambda argv: "Docker Compose v2")
    monkeypatch.setattr(
        doctor,
        "_device_group",
        lambda path: "dialout" if path.name.startswith("tty") else "plugdev",
    )
    report = doctor.dependency_report(settings, target_registry)
    assert report.ok
    assert all(check.ok for check in report.checks if check.required)
    assert doctor.device_modes_ok(manifest, settings)
    assert "0660" in doctor.device_mode_summary(manifest, settings)
    board_node.chmod(0o666)
    assert not doctor.device_modes_ok(manifest, settings)
    assert doctor.directory_access(settings.workspace_root, write=True)
    assert doctor.tool_ok({item.name: item for item in manifest.tools}, "JLinkExe")
    assert doctor.tool_path({}, "missing") is None
    assert doctor.tool_version({}, "missing") is None


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
        "void jlink_mcp_breakpoint_site(void) {}\n",
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


def test_generic_elf_inspect_and_register(tmp_path: Path) -> None:
    elf = _build_synthetic_fixture(tmp_path)
    inspection = inspect_elf(elf)
    assert inspection["entry"] == 0x08000000
    assert "jlink_mcp_manifest" in inspection["test_symbols"]
    artifact = registerable_artifact(elf, kind="elf")
    assert artifact.sha256 == hashlib.sha256(elf.read_bytes()).hexdigest()
    invalid = tmp_path / "not-elf"
    invalid.write_bytes(b"not elf")
    with pytest.raises(ELFError):
        inspect_elf(invalid)


@pytest.mark.asyncio
async def test_bearer_auth_health_and_passthrough() -> None:
    seen = []

    async def app(scope, receive, send):
        seen.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    wrapper = BearerTokenASGI(app, "secret")

    async def invoke(path, authorization=None, scope_type="http"):
        messages = []
        headers = [] if authorization is None else [(b"authorization", authorization)]

        async def send(message):
            messages.append(message)

        await wrapper(
            {"type": scope_type, "path": path, "headers": headers},
            lambda: None,
            send,
        )
        return messages

    assert (await invoke("/healthz"))[0]["status"] == 200
    unauthorized = await invoke("/mcp", b"Bearer wrong")
    assert unauthorized[0]["status"] == 401
    authorized = await invoke("/mcp", b"Bearer secret")
    assert authorized[0]["status"] == 204
    await invoke("/mcp", scope_type="websocket")
    assert len(seen) == 2


def test_mcp_runtime_registers_complete_tool_surface(settings) -> None:
    runtime = MCPRuntime(settings)
    tools = asyncio.run(runtime.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == CORE_TOOL_NAMES
    assert not runtime.registry.targets.profiles
    assert runtime.extensions.loaded_ids == []
    clear = next(tool for tool in tools if tool.name == "clear_breakpoint")
    assert clear.annotations.destructiveHint is True
    asyncio.run(runtime.service.close())
