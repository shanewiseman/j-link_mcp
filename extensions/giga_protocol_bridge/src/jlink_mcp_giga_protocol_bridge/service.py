"""Identity-gated, audited protocol bridge service built on public core primitives."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from jlink_mcp.models import CommandResult
from jlink_mcp.profiles import TargetProfile

from .backend import ProtocolBridgeBackend
from .config import GigaProtocolBridgeConfig
from .models import (
    BRIDGE_WIRE_VERSION,
    BlePairRequest,
    BridgeProtocol,
    DeviceSelector,
    ProtocolBridgeControlRequest,
    ProtocolBridgeExchangeRequest,
    ProtocolBridgeReceiveRequest,
    ProtocolBridgeResult,
    ProtocolBridgeStatus,
    WifiConnectRequest,
    decode_canonical_base64,
    encode_canonical_base64,
)
from .profiles import load_bridge_profiles


class ProtocolBridgeService:
    def __init__(
        self,
        jlink: Any,
        backend: ProtocolBridgeBackend,
        config: GigaProtocolBridgeConfig,
        target_profile: TargetProfile,
    ) -> None:
        self.jlink = jlink
        self.backend = backend
        self.config = config
        self.target_profile = target_profile

    async def _request(
        self,
        request: Any,
        *,
        selector: DeviceSelector | None,
        action: str,
        destructive: bool,
        operation: str | None = None,
        secrets_to_send: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> CommandResult:
        resolved = await self.jlink.resolve_selector_wait(selector)
        if (
            resolved.target_profile != self.target_profile.id
            or resolved.core != self.target_profile.default_core
        ):
            raise ValueError(
                "the protocol bridge control plane requires the GIGA primary core"
            )

        async def execute(port: str) -> CommandResult:
            return await self.backend.request(
                port,
                request,
                operation=operation,
                secrets_to_send=secrets_to_send,
                timeout=timeout,
            )

        public_request = (
            request.model_dump(mode="json", exclude_none=True)
            if hasattr(request, "model_dump")
            else dict(request)
        )
        return await self.jlink.audited_serial_operation(
            execute,
            selector=resolved,
            action=action,
            destructive=destructive,
            timeout=timeout,
            request={
                "operation": operation,
                "request": public_request,
                "secret_profile_fields": sorted(secrets_to_send or {}),
            },
        )

    @staticmethod
    def _response(result: CommandResult) -> dict[str, Any]:
        if not result.ok:
            raise RuntimeError(result.stderr or "protocol bridge request failed")
        response = result.parsed.get("bridge")
        if not isinstance(response, dict):
            raise TypeError("protocol bridge returned no structured response")
        return response

    async def status(
        self, *, selector: DeviceSelector | None = None
    ) -> ProtocolBridgeStatus:
        result = await self._request(
            {},
            selector=selector,
            action="protocol_bridge_status",
            destructive=False,
            operation="get_status",
        )
        response = self._response(result)
        metadata = response.get("metadata", {})
        if metadata.get("wire_version") != BRIDGE_WIRE_VERSION:
            raise RuntimeError("protocol bridge wire-version handshake failed")
        return ProtocolBridgeStatus.model_validate({**metadata, "command": result})

    async def control(
        self,
        request: ProtocolBridgeControlRequest,
        *,
        selector: DeviceSelector | None = None,
    ) -> ProtocolBridgeResult:
        secrets_to_send: dict[str, str] = {}
        if isinstance(request, WifiConnectRequest):
            profiles = load_bridge_profiles(self.config.profiles_file)
            try:
                profile = profiles.wifi[request.profile]
            except KeyError as exc:
                raise ValueError(
                    f"unknown Wi-Fi credential profile: {request.profile}"
                ) from exc
            secrets_to_send = {
                "ssid": profile.ssid.get_secret_value(),
                "password": profile.password.get_secret_value(),
            }
        elif isinstance(request, BlePairRequest) and request.passkey_profile:
            profiles = load_bridge_profiles(self.config.profiles_file)
            try:
                profile = profiles.ble_passkeys[request.passkey_profile]
            except KeyError as exc:
                raise ValueError(
                    f"unknown BLE passkey profile: {request.passkey_profile}"
                ) from exc
            secrets_to_send = {"passkey": profile.passkey.get_secret_value()}
        result = await self._request(
            request,
            selector=selector,
            action=f"protocol_bridge_control:{request.operation}",
            destructive=True,
            secrets_to_send=secrets_to_send or None,
            timeout=(
                35.0
                if request.operation in {"wifi_connect", "ble_scan", "ble_connect"}
                else 5.0
            ),
        )
        return self._result(request.operation, result)

    async def exchange(
        self,
        request: ProtocolBridgeExchangeRequest,
        *,
        selector: DeviceSelector | None = None,
    ) -> ProtocolBridgeResult:
        result = await self._request(
            request,
            selector=selector,
            action=f"protocol_bridge_exchange:{request.operation}",
            destructive=True,
            timeout=30.0,
        )
        return self._result(request.operation, result)

    async def receive(
        self,
        request: ProtocolBridgeReceiveRequest,
        *,
        selector: DeviceSelector | None = None,
    ) -> ProtocolBridgeResult:
        result = await self._request(
            request,
            selector=selector,
            action=f"protocol_bridge_receive:{request.protocol}",
            destructive=request.drain,
            operation="receive",
            timeout=max(5.0, request.timeout_ms / 1000 + 2.0),
        )
        return self._result(f"{request.protocol}_receive", result)

    @staticmethod
    def _result(operation: str, result: CommandResult) -> ProtocolBridgeResult:
        response = ProtocolBridgeService._response(result)
        data = decode_canonical_base64(response.get("data_base64", ""))
        protocol_name = operation.split("_", 1)[0]
        if protocol_name not in {item.value for item in BridgeProtocol}:
            protocol_name = "usb" if operation.startswith("usb_") else "gpio"
        metadata = dict(response.get("metadata", {}))
        if response.get("queue_depth") is not None:
            metadata["queue_depth"] = response["queue_depth"]
        if response.get("overflow_count") is not None:
            metadata["overflow_count"] = response["overflow_count"]
        return ProtocolBridgeResult(
            protocol=BridgeProtocol(protocol_name),
            operation=operation,
            data_base64=encode_canonical_base64(data),
            byte_count=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            hex_preview=data[:64].hex(),
            metadata=metadata,
            timestamp=datetime.now(UTC),
            overflow=bool(response.get("overflow_count")),
            command=result,
        )
