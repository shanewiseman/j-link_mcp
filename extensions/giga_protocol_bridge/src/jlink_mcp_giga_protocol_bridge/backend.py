"""Opaque binary serial backend for the versioned GIGA protocol bridge."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from typing import Any

from pydantic import BaseModel

from jlink_mcp.backends.serial import SerialBackend
from jlink_mcp.models import CommandResult

from .wire import (
    BridgeWireError,
    MessageType,
    decode_response_body,
    decode_stream,
    encode_message,
    encode_request_body,
    reassemble_frames,
)


class ProtocolBridgeBackend:
    name = "giga-protocol-bridge"

    def __init__(self, serial_backend: SerialBackend) -> None:
        self.serial = serial_backend
        self._port_locks: dict[str, asyncio.Lock] = {}

    async def request(
        self,
        port: str,
        request: BaseModel | dict[str, Any],
        *,
        operation: str | None = None,
        secrets_to_send: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> CommandResult:
        lock = self._port_locks.setdefault(port, asyncio.Lock())
        async with lock:
            return await self._request_locked(
                port,
                request,
                operation=operation,
                secrets_to_send=secrets_to_send,
                timeout=timeout,
            )

    async def _request_locked(
        self,
        port: str,
        request: BaseModel | dict[str, Any],
        *,
        operation: str | None,
        secrets_to_send: dict[str, str] | None,
        timeout: float,
    ) -> CommandResult:
        request_id = secrets.randbits(32)
        body = encode_request_body(
            request, operation=operation, secrets=secrets_to_send
        )
        encoded = encode_message(
            body, message_type=MessageType.REQUEST, request_id=request_id
        )
        serial_result, raw = await self.serial.exchange_binary(
            port, write=encoded, duration=timeout
        )
        serial_result.backend = self.name
        serial_result.command = [
            "protocol-bridge",
            port,
            f"request-id:{request_id}",
        ]
        serial_result.parsed.update(
            {
                "request_id": request_id,
                "request_body_bytes": len(body),
            }
        )
        if secrets_to_send:
            for wire_hash in (
                "request_sha256",
                "request_body_sha256",
                "response_sha256",
                "response_body_sha256",
            ):
                serial_result.parsed.pop(wire_hash, None)
        else:
            body_sha256 = hashlib.sha256(body).hexdigest()
            serial_result.command.append(f"body-sha256:{body_sha256}")
            serial_result.parsed["request_body_sha256"] = body_sha256
        if not serial_result.ok:
            return serial_result
        try:
            frames = decode_stream(raw)
            if not frames:
                raise BridgeWireError("bridge response contained no delimited frame")
            response_type = frames[0].message_type
            if response_type not in {MessageType.RESPONSE, MessageType.ERROR}:
                raise BridgeWireError(
                    f"unexpected bridge response type: {response_type.name}"
                )
            response_body = reassemble_frames(
                frames, request_id=request_id, message_type=response_type
            )
            response = decode_response_body(response_body)
        except BridgeWireError as exc:
            serial_result.return_code = 1
            serial_result.stderr = f"{type(exc).__name__}: {exc}"
            serial_result.parsed["wire_error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            return serial_result
        if secrets_to_send:
            sanitized_response = {"status": response["status"]}
            if response["status"]:
                sanitized_response["error"] = "bridge operation failed"
        else:
            # CommandResult.parsed is persisted and serialized as JSON. Keep the
            # canonical base64 field, never the duplicate arbitrary byte string.
            sanitized_response = {
                key: value for key, value in response.items() if key != "data"
            }
        response_metadata = {
            "wire_version": frames[0].wire_version,
            "message_type": response_type.name.lower(),
            "segments": len(frames),
            "response_body_bytes": len(response_body),
            "bridge": sanitized_response,
        }
        if not secrets_to_send:
            response_metadata["response_body_sha256"] = hashlib.sha256(
                response_body
            ).hexdigest()
        serial_result.parsed.update(response_metadata)
        if response_type == MessageType.ERROR or response["status"] != 0:
            serial_result.return_code = int(response["status"] or 1)
            serial_result.stderr = (
                "bridge operation failed"
                if secrets_to_send
                else response["error"] or "bridge operation failed"
            )
        return serial_result
