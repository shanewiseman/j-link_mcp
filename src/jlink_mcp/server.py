"""MCP tool/resource registration and authenticated HTTP application."""

from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import Settings
from .bridge_models import (
    ProtocolBridgeControlRequest,
    ProtocolBridgeDeployResult,
    ProtocolBridgeExchangeRequest,
    ProtocolBridgeReceiveRequest,
    ProtocolBridgeReleaseResult,
    ProtocolBridgeResult,
    ProtocolBridgeStatus,
)
from .models import (
    BuildResult,
    CommandResult,
    DependencyReport,
    DeviceSelector,
    TargetCore,
    ValidationReport,
)
from .service import JLinkService
from .workflows import Workflows

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
MUTATING = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)


class MCPRuntime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.service = JLinkService(self.settings)
        self.workflows = Workflows(self.service)
        self.mcp = FastMCP(
            "jlink-mcp",
            instructions=(
                "Capability-aware SEGGER J-Link control. Always call "
                "dependency_doctor and get_capabilities before target-changing "
                "operations. Use stable serial selectors and retain operation IDs "
                "as validation evidence."
            ),
            host=self.settings.host,
            port=self.settings.port,
            streamable_http_path=self.settings.mcp_path,
            json_response=True,
            stateless_http=False,
            lifespan=self._lifespan,
        )
        self._register_resources()
        self._register_tools()

    @asynccontextmanager
    async def _lifespan(self, _: FastMCP[Any]):
        try:
            yield {"service": self.service, "workflows": self.workflows}
        finally:
            await self.service.close()

    def _register_resources(self) -> None:
        @self.mcp.resource(
            "jlink://capabilities",
            name="J-Link capability manifest",
            mime_type="application/json",
        )
        def capability_resource() -> str:
            return self.service.capabilities().model_dump_json(indent=2)

        @self.mcp.resource(
            "jlink://audit/recent",
            name="Recent audit operations",
            mime_type="application/json",
        )
        def audit_resource() -> str:
            return json.dumps(self.service.store.list_operations(limit=100), indent=2)

        @self.mcp.resource(
            "jlink://sessions",
            name="Active probe leases",
            mime_type="application/json",
        )
        def session_resource() -> str:
            return json.dumps(
                [
                    {
                        "lease_id": lease.lease_id,
                        "probe_serial": lease.probe_serial,
                        "owner": lease.owner,
                        "acquired_at": lease.acquired_at.isoformat(),
                    }
                    for lease in self.service.leases.active_leases()
                ],
                indent=2,
            )

    def _register_tools(self) -> None:
        mcp = self.mcp
        service = self.service
        workflows = self.workflows

        @mcp.tool(annotations=READ_ONLY)
        def get_capabilities() -> dict[str, Any]:
            """Discover probes, boards, tools, workflows, and limitations."""
            return service.capabilities().model_dump(mode="json")

        @mcp.tool(annotations=READ_ONLY)
        def dependency_doctor() -> DependencyReport:
            """Check host, toolchain, device, permission, and GUI prerequisites."""
            return service.doctor()

        @mcp.tool(annotations=READ_ONLY)
        async def list_jlink_probes() -> CommandResult:
            """Ask J-Link Commander to enumerate attached probes."""
            return await service.probe_list()

        @mcp.tool(annotations=READ_ONLY)
        async def connect_target(
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Connect and return target voltage, identity, and registers."""
            return await service.connect(selector)

        @mcp.tool(annotations=READ_ONLY)
        async def disconnect_target(
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Confirm the selected probe has no persistent Commander session."""
            return await service.disconnect(selector)

        @mcp.tool(annotations=READ_ONLY)
        async def get_probe_information(
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Read probe firmware, hardware, licenses, voltage, and configuration."""
            return await service.probe_info(selector)

        @mcp.tool(annotations=MUTATING)
        async def reset_target(
            selector: DeviceSelector | None = None,
            halt: bool = False,
            reset_type: int | None = None,
        ) -> CommandResult:
            """Reset and halt/run; reset type 2 explicitly uses the reset pin."""
            return await service.reset(
                selector, halt=halt, reset_type=reset_type
            )

        @mcp.tool(annotations=MUTATING)
        async def halt_target(
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Halt the selected core and return registers."""
            return await service.halt(selector)

        @mcp.tool(annotations=MUTATING)
        async def run_target(
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Resume the selected core."""
            return await service.go(selector)

        @mcp.tool(annotations=MUTATING)
        async def step_target(
            count: int = 1, selector: DeviceSelector | None = None
        ) -> CommandResult:
            """Halt and single-step the selected core."""
            return await service.step(selector, count=count)

        @mcp.tool(annotations=READ_ONLY)
        async def read_memory(
            address: int,
            count: int = 1,
            width: int = 32,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Read target memory using 8-, 16-, or 32-bit accesses."""
            return await service.read_memory(
                address, count=count, width=width, selector=selector
            )

        @mcp.tool(annotations=MUTATING)
        async def write_memory(
            address: int,
            values: list[int],
            width: int = 32,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Write target memory using 8-, 16-, or 32-bit accesses."""
            return await service.write_memory(
                address, values, width=width, selector=selector
            )

        @mcp.tool(annotations=READ_ONLY)
        async def read_register(
            name: str, selector: DeviceSelector | None = None
        ) -> CommandResult:
            """Read one named CPU register."""
            return await service.read_register(name, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def write_register(
            name: str, value: int, selector: DeviceSelector | None = None
        ) -> CommandResult:
            """Write one named CPU register."""
            return await service.write_register(name, value, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def set_breakpoint(
            address: int, selector: DeviceSelector | None = None
        ) -> CommandResult:
            """Set a breakpoint at a target address."""
            return await service.set_breakpoint(address, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def clear_breakpoint(
            handle: int, selector: DeviceSelector | None = None
        ) -> CommandResult:
            """Clear a breakpoint using the handle returned by J-Link."""
            return await service.clear_breakpoint(handle, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def set_watchpoint(
            address: int,
            access: str = "W",
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Set a read or write data watchpoint."""
            return await service.set_watchpoint(
                address, access=access, selector=selector
            )

        @mcp.tool(annotations=MUTATING)
        async def clear_watchpoint(
            handle: int, selector: DeviceSelector | None = None
        ) -> CommandResult:
            """Clear a watchpoint using the handle returned by J-Link."""
            return await service.clear_watchpoint(handle, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def erase_flash(
            start_address: int | None = None,
            end_address: int | None = None,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Erase all application flash or an explicit address range."""
            return await service.erase_flash(
                start_address, end_address, selector=selector
            )

        @mcp.tool(annotations=READ_ONLY)
        async def verify_binary(
            artifact_path: str,
            address: int,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Compare a raw BIN against target memory at an explicit address."""
            return await service.verify_binary(
                artifact_path, address, selector=selector
            )

        @mcp.tool(annotations=MUTATING)
        async def raw_commander(
            commands: list[str],
            selector: DeviceSelector | None = None,
            destructive: bool = True,
            timeout_seconds: float | None = None,
        ) -> CommandResult:
            """Run validated J-Link Commander commands without a host shell."""
            return await service.raw(
                commands,
                selector=selector,
                destructive=destructive,
                timeout=timeout_seconds,
            )

        @mcp.tool(annotations=MUTATING)
        async def raw_jlink_command_string(
            command: str,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Execute one validated J-Link DLL command string through Commander."""
            return await service.command_string(command, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def run_segger_application(
            application: str,
            args: list[str],
            timeout_seconds: float = 30,
            destructive: bool = True,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Run a finite allowlisted SEGGER application mode without a shell."""
            return await service.run_application(
                application,
                args,
                timeout=timeout_seconds,
                destructive=destructive,
                selector=selector,
            )

        @mcp.tool(annotations=MUTATING)
        async def serial_exchange(
            write: str | None = None,
            baudrate: int = 115200,
            duration_seconds: float = 2.0,
            until: str | None = None,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Capture USB serial and optionally send one newline-framed request."""
            return await service.serial_exchange(
                selector=selector,
                write=write,
                baudrate=baudrate,
                duration=duration_seconds,
                until=until,
            )

        @mcp.tool(annotations=READ_ONLY)
        async def capture_serial(
            baudrate: int = 115200,
            duration_seconds: float = 2.0,
            until: str | None = None,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Capture USB serial without writing to the target."""
            return await service.serial_exchange(
                selector=selector,
                baudrate=baudrate,
                duration=duration_seconds,
                until=until,
            )

        @mcp.tool(annotations=MUTATING)
        async def deploy_protocol_bridge(
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeDeployResult:
            """Back up full GIGA flash, deploy the checked-in bridge HEX, and handshake."""
            return await workflows.deploy_protocol_bridge(selector=selector)

        @mcp.tool(annotations=READ_ONLY)
        async def get_protocol_bridge_status(
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeStatus:
            """Read bridge identity, interfaces, ownership, connections, and queues."""
            return await service.protocol_bridge_status(selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def protocol_bridge_control(
            request: ProtocolBridgeControlRequest,
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeResult:
            """Configure bridge transports, resources, devices, radios, and sockets."""
            return await service.protocol_bridge_control(request, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def protocol_bridge_exchange(
            request: ProtocolBridgeExchangeRequest,
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeResult:
            """Exchange opaque base64 payload bytes over one selected interface."""
            return await service.protocol_bridge_exchange(request, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def protocol_bridge_receive(
            request: ProtocolBridgeReceiveRequest,
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeResult:
            """Poll or drain queued UART, CAN, USB, Wi-Fi, BLE, or GPIO events."""
            return await service.protocol_bridge_receive(request, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def swo_control(
            action: str,
            speed_hz: int | None = None,
            capture_ms: int = 500,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """List SWO speeds, query status, capture ITM/SWO, or stop capture."""
            return await service.swo(
                action,
                speed_hz=speed_hz,
                capture_ms=capture_ms,
                selector=selector,
            )

        @mcp.tool(annotations=MUTATING)
        async def start_gdb_session(
            selector: DeviceSelector | None = None,
            elf_path: str | None = None,
        ) -> dict[str, Any]:
            """Start managed J-Link GDB Server and GDB/MI sessions."""
            return await service.start_gdb(selector=selector, elf_path=elf_path)

        @mcp.tool(annotations=MUTATING)
        async def gdb_command(
            session_id: str, command: str, timeout_seconds: float = 30
        ) -> CommandResult:
            """Execute a validated GDB/MI or monitor command."""
            return await service.gdb_command(
                session_id, command, timeout=timeout_seconds
            )

        @mcp.tool(annotations=READ_ONLY)
        async def capture_gdb_channel(
            session_id: str,
            channel: str,
            duration_seconds: float = 2.0,
        ) -> CommandResult:
            """Read GDB Server RTT, raw SWO, or semihosting TCP data."""
            return await service.capture_gdb_channel(
                session_id,
                channel,
                duration=duration_seconds,
            )

        @mcp.tool(annotations=MUTATING)
        async def exchange_gdb_channel(
            session_id: str,
            channel: str,
            write: str,
            duration_seconds: float = 2.0,
        ) -> CommandResult:
            """Write and then capture a managed RTT/SWO/semihosting channel."""
            return await service.capture_gdb_channel(
                session_id, channel, duration=duration_seconds, write=write
            )

        @mcp.tool(annotations=MUTATING)
        async def stop_gdb_session(
            session_id: str, resume_target: bool = True
        ) -> dict[str, Any]:
            """Stop a managed GDB session and optionally resume the target."""
            await service.stop_gdb(session_id, resume=resume_target)
            return {"session_id": session_id, "stopped": True}

        @mcp.tool(annotations=READ_ONLY)
        def inspect_elf(elf_path: str) -> dict[str, Any]:
            """Inspect ELF segments, entry point, and jlink_mcp_* symbols."""
            return service.inspect_elf(elf_path)

        @mcp.tool(annotations=READ_ONLY)
        async def build_giga_firmware(
            sketch_path: str,
            core: TargetCore,
            flash_split: str = "75_25",
            clean: bool = True,
        ) -> BuildResult:
            """Compile a GIGA M7 or M4 sketch and register all artifacts."""
            return await workflows.build_firmware(
                sketch_path, core=core, flash_split=flash_split, clean=clean
            )

        @mcp.tool(annotations=READ_ONLY)
        async def build_protocol_bridge_release(
            verify_checked_in: bool = True,
        ) -> ProtocolBridgeReleaseResult:
            """Build a deterministic state bundle and compare the checked-in HEX."""
            return await workflows.build_protocol_bridge_release(
                verify_checked_in=verify_checked_in
            )

        @mcp.tool(annotations=MUTATING)
        async def flash_and_verify(
            artifact_path: str, selector: DeviceSelector | None = None
        ) -> CommandResult:
            """Flash an ELF/HEX artifact, verify it, reset, and run."""
            return await workflows.flash_and_verify(
                artifact_path, selector=selector
            )

        @mcp.tool(annotations=MUTATING)
        async def flash_binary(
            artifact_path: str,
            address: int,
            selector: DeviceSelector | None = None,
        ) -> CommandResult:
            """Flash and verify a raw binary at an explicit address."""
            return await workflows.flash_binary(
                artifact_path, address, selector=selector
            )

        @mcp.tool(annotations=READ_ONLY)
        async def backup_flash(
            address: int,
            size: int,
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Read a bounded flash region into a hashed artifact."""
            result, artifact = await workflows.backup_flash(
                address, size, selector=selector
            )
            return {
                "command": result.model_dump(mode="json"),
                "artifact": artifact.model_dump(mode="json") if artifact else None,
            }

        @mcp.tool(annotations=MUTATING)
        async def validate_giga_fixture(
            selector: DeviceSelector | None = None,
            m7_sketch: str = "firmware/giga_hil/m7",
            m4_sketch: str = "firmware/giga_hil/m4",
        ) -> ValidationReport:
            """Run the MCP-owned GIGA fixture build/flash validation workflow."""
            return await workflows.validate_fixture(
                selector=selector, m7_sketch=m7_sketch, m4_sketch=m4_sketch
            )

        @mcp.tool(annotations=MUTATING)
        async def hardware_preflight(
            selector: DeviceSelector | None = None,
            prepare_dual_core: bool = False,
        ) -> dict[str, Any]:
            """Identify both cores and snapshot state, optionally releasing held M4."""
            return await workflows.hardware_preflight(
                selector=selector, prepare_dual_core=prepare_dual_core
            )

        @mcp.tool(annotations=MUTATING)
        async def prepare_giga_dual_core_debug(
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Transiently release an option-held GIGA M4 and verify both cores."""
            return await workflows.prepare_giga_dual_core_debug(selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def deploy_dual_core_firmware(
            selector: DeviceSelector | None = None,
            m7_sketch: str = "firmware/giga_hil/m7",
            m4_sketch: str = "firmware/giga_hil/m4",
            flash_split: str = "75_25",
        ) -> dict[str, Any]:
            """Build, flash, verify, reset, and run both GIGA cores."""
            return await workflows.dual_core_deploy(
                selector=selector,
                m7_sketch=m7_sketch,
                m4_sketch=m4_sketch,
                flash_split=flash_split,
            )

        @mcp.tool(annotations=MUTATING)
        async def boot_and_observe(
            selector: DeviceSelector | None = None,
            m7_elf_path: str | None = None,
            m4_elf_path: str | None = None,
        ) -> dict[str, Any]:
            """Reset/run both cores and validate manifests, heartbeat, self-test, and RPC."""
            return await workflows.boot_and_observe(
                selector=selector,
                m7_elf_path=m7_elf_path,
                m4_elf_path=m4_elf_path,
            )

        @mcp.tool(annotations=MUTATING)
        async def assert_debug_fixture(
            elf_path: str,
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Assert symbolic breakpoint/watchpoint, registers, stack, memory, and step."""
            return await workflows.debug_fixture(elf_path, selector=selector)

        @mcp.tool(annotations=MUTATING)
        async def capture_controlled_crash(
            elf_path: str,
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Trigger, capture, analyze, and recover a fixture HardFault."""
            return await workflows.crash_capture(elf_path, selector=selector)

        @mcp.tool(annotations=READ_ONLY)
        async def compare_firmware(
            artifact_path: str,
            address: int,
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Compare a BIN artifact against target flash and return hashes/evidence."""
            return await workflows.compare_firmware(
                artifact_path, address, selector=selector
            )

        @mcp.tool(annotations=READ_ONLY)
        async def compare_backup_region(
            backup_path: str,
            backup_offset: int,
            address: int,
            size: int,
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Compare target bytes with a bounded slice of a full BIN backup."""
            return await workflows.compare_backup_region(
                backup_path,
                backup_offset,
                address,
                size,
                selector=selector,
            )

        @mcp.tool(annotations=MUTATING)
        async def capture_rtt(
            elf_path: str,
            selector: DeviceSelector | None = None,
            duration_seconds: float = 3.0,
            channel: int = 0,
        ) -> dict[str, Any]:
            """Capture bounded RTT using the ELF-derived control block address."""
            return await workflows.capture_rtt(
                elf_path,
                selector=selector,
                duration_seconds=duration_seconds,
                channel=channel,
            )

        @mcp.tool(annotations=MUTATING)
        async def restore_flash_backup(
            backup_path: str,
            address: int,
            expected_sha256: str,
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Restore a hash-authorized BIN backup and verify every programmed byte."""
            return await workflows.restore_backup(
                backup_path,
                address,
                expected_sha256,
                selector=selector,
            )

        @mcp.tool(annotations=READ_ONLY)
        async def generate_validation_report(
            title: str = "J-Link MCP validation", audit_limit: int = 1000
        ) -> dict[str, Any]:
            """Generate machine-readable and Markdown reports from persisted evidence."""
            return await workflows.generate_validation_report(
                title=title, audit_limit=audit_limit
            )

        @mcp.tool(annotations=MUTATING)
        async def launch_segger_gui(
            application: str,
            args: list[str] | None = None,
            selector: DeviceSelector | None = None,
        ) -> dict[str, Any]:
            """Launch an allowlisted SEGGER GUI in isolated Xvfb."""
            return await service.start_gui(
                application, args or [], selector=selector
            )

        @mcp.tool(annotations=MUTATING)
        async def gui_keys(session_id: str, keys: str) -> CommandResult:
            """Send an xdotool key sequence to the isolated GUI."""
            return await service.gui_keys(session_id, keys)

        @mcp.tool(annotations=MUTATING)
        async def gui_click(session_id: str, x: int, y: int) -> CommandResult:
            """Click coordinates in the isolated GUI display."""
            return await service.gui_click(session_id, x, y)

        @mcp.tool(annotations=READ_ONLY)
        async def gui_screenshot(session_id: str) -> CommandResult:
            """Capture the isolated GUI display as evidence."""
            return await service.gui_screenshot(session_id)

        @mcp.tool(annotations=READ_ONLY)
        async def gui_ocr(screenshot_path: str) -> CommandResult:
            """OCR a screenshot within the configured state/workspace roots."""
            return await service.gui_ocr(screenshot_path)

        @mcp.tool(annotations=READ_ONLY)
        async def gui_accessibility_tree(session_id: str) -> CommandResult:
            """Inspect semantic AT-SPI roles, names, states, and hierarchy."""
            return await service.gui_accessibility(session_id)

        @mcp.tool(annotations=READ_ONLY)
        def gui_session_info(session_id: str) -> dict[str, Any]:
            """Return application, PID, display, and live GUI process state."""
            return service.gui_session_info(session_id)

        @mcp.tool(annotations=READ_ONLY)
        async def gui_image_match(
            session_id: str, template_path: str, threshold: float = 0.85
        ) -> CommandResult:
            """Locate a confined template in a fresh GUI screenshot."""
            template = service.settings.resolve_allowed_path(template_path)
            return await service.gui_image_match(
                session_id, template, threshold=threshold
            )

        @mcp.tool(annotations=MUTATING)
        async def stop_segger_gui(session_id: str) -> dict[str, Any]:
            """Stop an isolated SEGGER GUI session."""
            await service.stop_gui(session_id)
            return {"session_id": session_id, "stopped": True}

        @mcp.tool(annotations=READ_ONLY)
        def recent_audit_operations(limit: int = 100) -> list[dict[str, Any]]:
            """Return recent hash-chained operation records."""
            return service.store.list_operations(limit=limit)

        @mcp.tool(annotations=READ_ONLY)
        def verify_audit_chain() -> dict[str, Any]:
            """Verify the immutable operation hash chain."""
            ok, error = service.store.verify_chain()
            return {"ok": ok, "error": error}


class BearerTokenASGI:
    """Minimal static bearer authentication around the MCP ASGI application."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") == "/healthz":
            body = b'{"status":"ok"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        expected = b"Bearer " + self.token
        supplied = headers.get(b"authorization", b"")
        if not secrets.compare_digest(supplied, expected):
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def create_http_app(settings: Settings | None = None) -> BearerTokenASGI:
    runtime = MCPRuntime(settings)
    token = runtime.settings.bearer_token(required=True)
    assert token is not None
    return BearerTokenASGI(runtime.mcp.streamable_http_app(), token)
