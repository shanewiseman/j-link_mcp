"""Registration lifecycle for the optional GIGA protocol bridge extension."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.types import ToolAnnotations

from jlink_mcp.extensions import (
    EXTENSION_API_VERSION,
    CapabilityContribution,
    ExtensionContext,
)
from jlink_mcp.models import (
    CapabilityAvailability,
    CapabilityManifest,
    CapabilityState,
    DependencyCheck,
)
from jlink_mcp.profiles import TargetProfile

from .backend import ProtocolBridgeBackend
from .config import GigaProtocolBridgeConfig
from .models import (
    BRIDGE_FIRMWARE_VERSION,
    BRIDGE_WIRE_VERSION,
    SAFE_GPIO_PINS,
    BridgeProtocol,
    DeviceSelector,
    ProtocolBridgeControlRequest,
    ProtocolBridgeDeployResult,
    ProtocolBridgeExchangeRequest,
    ProtocolBridgeReceiveRequest,
    ProtocolBridgeReleaseResult,
    ProtocolBridgeResult,
    ProtocolBridgeStatus,
)
from .service import ProtocolBridgeService
from .workflows import (
    ProtocolBridgeWorkflows,
    _release_checksums_authorize,
)

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


class GigaProtocolBridgeExtension:
    id = "giga_protocol_bridge"
    version = "0.1.0"
    api_version = EXTENSION_API_VERSION
    dependencies = ("arduino_giga",)
    config_model = GigaProtocolBridgeConfig

    def register(self, context: ExtensionContext) -> None:
        config = GigaProtocolBridgeConfig.model_validate(context.config)
        giga_workflows = context.require_extension_service("arduino_giga", "workflows")
        giga_config = context.require_extension_service("arduino_giga", "config")
        giga_profile = context.require_extension_service("arduino_giga", "profile")
        if not isinstance(giga_profile, TargetProfile):
            raise TypeError("arduino_giga profile service is not a TargetProfile")
        backend = ProtocolBridgeBackend(context.services.serial)
        service = ProtocolBridgeService(
            context.services.jlink,
            backend,
            config,
            giga_profile,
        )
        workflows = ProtocolBridgeWorkflows(
            context.services.jlink,
            service,
            giga_workflows,
            giga_config,
            giga_profile,
        )
        context.register_capability_provider(_capabilities)
        context.register_dependency_provider(
            lambda manifest: _dependencies(giga_config.user_root)
        )
        context.publish_service("bridge", service)
        context.publish_service("workflows", workflows)
        self._register_resource(context)
        self._register_tools(context, service, workflows)

    def shutdown(self) -> None:
        return None

    @staticmethod
    def _register_resource(context: ExtensionContext) -> None:
        @context.register_resource(
            "jlink://extensions/giga_protocol_bridge/protocols",
            name="GIGA protocol bridge contract",
            mime_type="application/json",
        )
        def protocol_resource() -> str:
            return json.dumps(
                {
                    "firmware_version": BRIDGE_FIRMWARE_VERSION,
                    "wire_version": BRIDGE_WIRE_VERSION,
                    "protocols": [item.value for item in BridgeProtocol],
                    "safe_gpio_pins": list(SAFE_GPIO_PINS),
                },
                indent=2,
            )

    @staticmethod
    def _register_tools(
        context: ExtensionContext,
        service: ProtocolBridgeService,
        workflows: ProtocolBridgeWorkflows,
    ) -> None:
        @context.register_tool(annotations=MUTATING)
        async def deploy_protocol_bridge(
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeDeployResult:
            """Back up full GIGA flash, deploy the checked-in bridge HEX, and handshake."""
            return await workflows.deploy_protocol_bridge(selector=selector)

        @context.register_tool(annotations=READ_ONLY)
        async def get_protocol_bridge_status(
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeStatus:
            """Read bridge identity, interfaces, ownership, connections, and queues."""
            return await service.status(selector=selector)

        @context.register_tool(annotations=MUTATING)
        async def protocol_bridge_control(
            request: ProtocolBridgeControlRequest,
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeResult:
            """Configure bridge transports, resources, devices, radios, and sockets."""
            return await service.control(request, selector=selector)

        @context.register_tool(annotations=MUTATING)
        async def protocol_bridge_exchange(
            request: ProtocolBridgeExchangeRequest,
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeResult:
            """Exchange opaque base64 payload bytes over one selected interface."""
            return await service.exchange(request, selector=selector)

        @context.register_tool(annotations=MUTATING)
        async def protocol_bridge_receive(
            request: ProtocolBridgeReceiveRequest,
            selector: DeviceSelector | None = None,
        ) -> ProtocolBridgeResult:
            """Poll or drain queued UART, CAN, USB, Wi-Fi, BLE, or GPIO events."""
            return await service.receive(request, selector=selector)

        @context.register_tool(annotations=READ_ONLY)
        async def build_protocol_bridge_release(
            verify_checked_in: bool = True,
        ) -> ProtocolBridgeReleaseResult:
            """Build a deterministic state bundle and compare the checked-in HEX."""
            return await workflows.build_protocol_bridge_release(
                verify_checked_in=verify_checked_in
            )


def _capabilities(manifest: CapabilityManifest) -> CapabilityContribution:
    base = manifest.workflows
    serial_available = base.get("serial") == CapabilityState.AVAILABLE
    flash_available = base.get("flash_verify") == CapabilityState.AVAILABLE
    build_available = base.get("build_firmware") == CapabilityState.AVAILABLE

    def state(value: bool) -> CapabilityState:
        return CapabilityState.AVAILABLE if value else CapabilityState.UNAVAILABLE

    workflows = {
        "protocol_bridge_release": state(build_available),
        "protocol_bridge_deploy": state(flash_available and serial_available),
        "protocol_bridge": state(flash_available and serial_available),
    }

    def detail(name: str, dependencies: list[str], reason: str):
        return CapabilityAvailability(
            state=workflows[name],
            dependencies=dependencies,
            reason=None if workflows[name] == CapabilityState.AVAILABLE else reason,
        )

    fixtures = {
        "spi": "wired SPI responder and loopback fixture",
        "i2c": "wired I2C target fixture",
        "uart": "wired UART loopback or peer fixture",
        "can": "two terminated external CAN transceivers and CAN peer",
        "usb": "supported non-hub USB device on the host connector",
        "wifi": "reachable Wi-Fi profile and TCP/UDP peer",
        "ble": "reachable BLE peripheral fixture",
        "gpio": "wired GPIO loopback fixture",
    }
    return CapabilityContribution(
        workflows=workflows,
        workflow_details={
            "protocol_bridge_release": detail(
                "protocol_bridge_release",
                ["arduino-cli", "pinned board platform", "pinned bridge libraries"],
                "The pinned bridge build stack is unavailable",
            ),
            "protocol_bridge_deploy": detail(
                "protocol_bridge_deploy",
                ["JLinkExe", "board serial", "verified bridge release"],
                "Commander, board serial, and a verified release are required",
            ),
            "protocol_bridge": detail(
                "protocol_bridge",
                [
                    "positive primary-core identity",
                    "exclusive probe lease",
                    "board serial",
                ],
                "Positive target identity and an accessible serial channel are required",
            ),
        },
        features={
            f"protocol_bridge_hil_{protocol}": CapabilityAvailability(
                state=CapabilityState.UNKNOWN,
                dependencies=[fixture],
                reason=(
                    f"Physical {protocol.upper()} companion fixture presence and wiring "
                    "cannot be inferred; prove it with the opt-in HIL acceptance suite"
                ),
            )
            for protocol, fixture in fixtures.items()
        },
        limitations=[
            "Protocol-bridge exchanges are serialized and are not hard real-time operations.",
            "Protocol-bridge payloads are opaque bytes represented as canonical base64 at the MCP boundary.",
            "The GIGA Wi-Fi and BLE radios are mutually exclusive at runtime.",
        ],
        atomic_tools=[
            "get_protocol_bridge_status",
            "protocol_bridge_control",
            "protocol_bridge_exchange",
            "protocol_bridge_receive",
        ],
    )


def _dependencies(
    user_root: Path, *, firmware_root: Path | None = None
) -> list[DependencyCheck]:
    libraries = {
        "Arduino_USBHostMbed5": "0.3.1",
        "ArduinoBLE": "2.1.0",
        "Arduino_SpiNINA": "0.0.2",
    }
    checks = [
        DependencyCheck(
            name=f"arduino-library-{name.lower()}-{version}",
            ok=_library_version(user_root, name) == version,
            observed=_library_version(user_root, name),
            expected=f"{name}@{version}",
            remediation="Rebuild the pinned GIGA extension image.",
        )
        for name, version in libraries.items()
    ]
    firmware_root = (
        firmware_root or Path(__file__).parent / "firmware" / "protocol_bridge"
    )
    release = firmware_root / "release"
    hex_path = release / "protocol_bridge_m7.hex"
    manifest_path = release / "protocol_bridge_manifest.json"
    checksum_path = release / "SHA256SUMS"
    release_ok = _release_checksums_authorize(checksum_path, (hex_path, manifest_path))
    checks.append(
        DependencyCheck(
            name="protocol-bridge-release",
            ok=release_ok,
            observed=str(release),
            expected="checked-in bridge HEX, manifest, and authorizing checksum",
            remediation="Reinstall or rebuild the bridge extension package.",
        )
    )
    return checks


def _library_version(root: Path, name: str) -> str | None:
    properties = root / "libraries" / name / "library.properties"
    try:
        for line in properties.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "version":
                return value.strip()
    except OSError:
        return None
    return None
