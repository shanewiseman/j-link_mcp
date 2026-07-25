from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest
from jlink_mcp_giga_protocol_bridge.backend import ProtocolBridgeBackend
from jlink_mcp_giga_protocol_bridge.models import (
    BlePairRequest,
    CanSendRequest,
    GpioConfigureRequest,
    I2cExchangeRequest,
    ProtocolBridgeControlRequest,
    ProtocolBridgeExchangeRequest,
    SpiExchangeRequest,
    UsbSelectRequest,
    UsbTransferRequest,
    decode_canonical_base64,
    validate_safe_pin,
)
from jlink_mcp_giga_protocol_bridge.profiles import load_bridge_profiles
from jlink_mcp_giga_protocol_bridge.resources import (
    BridgeResourceConflict,
    BridgeResourceManager,
)
from jlink_mcp_giga_protocol_bridge.service import ProtocolBridgeService
from jlink_mcp_giga_protocol_bridge.wire import (
    BridgeCrcError,
    BridgeSequenceError,
    BridgeWireError,
    FieldId,
    MessageType,
    WireFrame,
    cobs_decode,
    cobs_encode,
    decode_frame,
    decode_response_body,
    decode_stream,
    decode_tlvs,
    encode_frame,
    encode_message,
    encode_request_body,
    encode_tlvs,
    reassemble_frames,
)
from pydantic import TypeAdapter, ValidationError

from jlink_mcp.backends.serial import SerialBackend

from .conftest import make_result


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def test_contracts_reject_ambiguous_payloads_and_protected_pins() -> None:
    assert decode_canonical_base64("AA==") == b"\x00"
    for invalid in ("AA", "A===", "not base64"):
        with pytest.raises(ValueError, match="canonical"):
            decode_canonical_base64(invalid)

    assert validate_safe_pin("d22") == "D22"
    for pin in ("D8", "D21", "D86", "D101", "PA0"):
        with pytest.raises(ValueError, match="protected"):
            validate_safe_pin(pin)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        GpioConfigureRequest.model_validate(
            {"operation": "gpio_configure", "pin": "D22", "mode": "input", "mystery": 1}
        )
    with pytest.raises(ValidationError, match="pull mode"):
        GpioConfigureRequest(
            operation="gpio_configure", pin="D22", mode="output", pull="up"
        )


def test_protocol_specific_contract_bounds() -> None:
    SpiExchangeRequest(
        operation="spi_exchange", bus=0, chip_select="D22", data_base64=_b64(b"abc")
    )
    with pytest.raises(ValidationError):
        SpiExchangeRequest(
            operation="spi_exchange",
            bus=0,
            chip_select="D22",
            data_base64=_b64(b"x" * 64_001),
        )
    with pytest.raises(ValidationError, match="32"):
        I2cExchangeRequest(
            operation="i2c_exchange",
            bus=0,
            address=0x50,
            data_base64=_b64(b"x" * 33),
        )
    with pytest.raises(ValidationError, match="standard CAN"):
        CanSendRequest(
            operation="can_send", bus=0, arbitration_id=0x800, data_base64=""
        )
    with pytest.raises(ValidationError, match="8 bytes"):
        CanSendRequest(
            operation="can_send", bus=0, arbitration_id=1, data_base64=_b64(b"x" * 9)
        )
    with pytest.raises(ValidationError, match="setup packet"):
        UsbTransferRequest(operation="usb_transfer", transfer_type="control")
    with pytest.raises(ValidationError, match="control-transfer only"):
        UsbTransferRequest(
            operation="usb_transfer", transfer_type="bulk", endpoint=0x81, request=1
        )
    with pytest.raises(ValidationError, match="4096"):
        UsbTransferRequest(
            operation="usb_transfer",
            transfer_type="interrupt",
            endpoint=0x81,
            read_length=4097,
        )
    stable_usb = UsbSelectRequest(
        operation="usb_select",
        vendor_id=0x2341,
        product_id=0x0266,
        serial="0045002B3333511632363530",
        interface_number=1,
    )
    assert stable_usb.serial == "0045002B3333511632363530"
    with pytest.raises(ValidationError):
        UsbSelectRequest(operation="usb_select", vendor_id=0x2341)


def test_every_control_and_exchange_discriminator_is_typed_and_extra_forbidden() -> (
    None
):
    controls = [
        {"operation": "uart_configure", "port": 0, "baudrate": 115200},
        {"operation": "can_configure", "bus": 0, "bitrate": 500000},
        {"operation": "usb_enumerate"},
        {"operation": "usb_select", "vendor_id": 0x2341, "product_id": 0x0266},
        {"operation": "usb_reset"},
        {"operation": "usb_release"},
        {"operation": "wifi_connect", "profile": "lab"},
        {"operation": "wifi_disconnect"},
        {
            "operation": "wifi_socket_open",
            "protocol": "tcp",
            "host": "192.0.2.1",
            "port": 7,
        },
        {"operation": "wifi_socket_close", "socket_id": 0},
        {"operation": "ble_scan"},
        {"operation": "ble_connect", "address": "AA:BB:CC:DD:EE:FF"},
        {"operation": "ble_disconnect"},
        {"operation": "ble_pair", "passkey_profile": "sensor"},
        {"operation": "ble_discover"},
        {
            "operation": "ble_subscribe",
            "service_uuid": "180F",
            "characteristic_uuid": "2A19",
        },
        {"operation": "gpio_configure", "pin": "D22", "mode": "input"},
        {"operation": "gpio_watch", "pin": "D22", "edge": "change"},
    ]
    exchanges = [
        {"operation": "spi_exchange", "bus": 0, "chip_select": "D22"},
        {"operation": "i2c_exchange", "bus": 0, "address": 0x50},
        {"operation": "uart_write", "port": 0},
        {"operation": "can_send", "bus": 0, "arbitration_id": 0x123},
        {
            "operation": "usb_transfer",
            "transfer_type": "control",
            "request_type": 0x80,
            "request": 6,
            "value": 0x0100,
            "index": 0,
        },
        {"operation": "wifi_send", "socket_id": 0},
        {
            "operation": "ble_read",
            "service_uuid": "180F",
            "characteristic_uuid": "2A19",
        },
        {
            "operation": "ble_write",
            "service_uuid": "180F",
            "characteristic_uuid": "2A19",
        },
        {"operation": "gpio_read", "pin": "D22"},
        {"operation": "gpio_write", "pin": "D22", "value": True},
        {
            "operation": "gpio_pulse",
            "pin": "D22",
            "value": True,
            "duration_us": 10,
        },
    ]
    control_adapter = TypeAdapter(ProtocolBridgeControlRequest)
    exchange_adapter = TypeAdapter(ProtocolBridgeExchangeRequest)
    assert [control_adapter.validate_python(item).operation for item in controls] == [
        item["operation"] for item in controls
    ]
    assert [exchange_adapter.validate_python(item).operation for item in exchanges] == [
        item["operation"] for item in exchanges
    ]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        control_adapter.validate_python({**controls[0], "unknown": True})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        exchange_adapter.validate_python({**exchanges[0], "unknown": True})


def test_wire_golden_vector_segmentation_crc_and_sequences() -> None:
    frame = WireFrame(
        message_type=MessageType.REQUEST,
        request_id=0x01020304,
        segment_index=0,
        segment_count=1,
        payload=b"\x00\x01\x00",
    )
    encoded = encode_frame(frame)
    assert encoded.hex() == "0b4a4c50420101040302010102010203056b08197302010100"
    assert decode_frame(encoded) == frame
    assert cobs_decode(cobs_encode(bytes(range(256)))) == bytes(range(256))

    decoded = bytearray(cobs_decode(encoded[:-1]))
    decoded[-1] ^= 0x01
    with pytest.raises(BridgeCrcError):
        decode_frame(cobs_encode(decoded))

    payload = bytes(range(256)) * 256
    stream = encode_message(payload, message_type=MessageType.RESPONSE, request_id=7)
    frames = decode_stream(stream)
    assert len(frames) > 1
    assert (
        reassemble_frames(frames, request_id=7, message_type=MessageType.RESPONSE)
        == payload
    )
    with pytest.raises(BridgeSequenceError, match="required segments"):
        reassemble_frames(frames[:-1], request_id=7)
    with pytest.raises(BridgeSequenceError, match="out-of-order"):
        reassemble_frames([frames[1], frames[0], *frames[2:]], request_id=7)
    with pytest.raises(BridgeSequenceError, match="stale"):
        reassemble_frames(frames, request_id=8)


def test_wire_rejects_malformed_unsupported_and_partial_frames() -> None:
    encoded = encode_frame(
        WireFrame(
            message_type=MessageType.RESPONSE,
            request_id=7,
            segment_index=0,
            segment_count=1,
            payload=b"abc",
        )
    )

    with pytest.raises(BridgeWireError, match="partial"):
        decode_stream(encoded[:-1])
    with pytest.raises(BridgeWireError, match="unsupported"):
        encode_frame(
            WireFrame(
                message_type=MessageType.RESPONSE,
                request_id=7,
                segment_index=0,
                segment_count=1,
                payload=b"abc",
                wire_version=2,
            )
        )

    def mutate(offset: int, value: int, *, refresh_crc: bool = True) -> bytes:
        decoded = bytearray(cobs_decode(encoded[:-1]))
        decoded[offset] = value
        if refresh_crc:
            crc = zlib.crc32(decoded[:16] + decoded[20:]) & 0xFFFFFFFF
            struct.pack_into("<I", decoded, 16, crc)
        return cobs_encode(decoded) + b"\x00"

    with pytest.raises(BridgeWireError, match="magic"):
        decode_frame(mutate(0, ord("X"), refresh_crc=False))
    with pytest.raises(BridgeWireError, match="unsupported bridge wire version"):
        decode_frame(mutate(4, 2))
    with pytest.raises(BridgeWireError, match="unknown bridge message type"):
        decode_frame(mutate(5, 99))

    malformed_length = bytearray(cobs_decode(encoded[:-1]))
    struct.pack_into("<H", malformed_length, 14, 4)
    with pytest.raises(BridgeWireError, match="payload length"):
        decode_frame(cobs_encode(malformed_length) + b"\x00")
    with pytest.raises(BridgeWireError, match="64 KiB"):
        encode_message(b"x" * 65_537, message_type=MessageType.REQUEST, request_id=7)


def test_tlv_and_operation_golden_vectors_fail_closed() -> None:
    assert encode_request_body({"operation": "gpio_read", "pin": "D22"}).hex() == (
        "010002003000280002001600"
    )
    duplicate = struct.pack("<HHBHHB", 1, 1, 2, 1, 1, 3)
    with pytest.raises(BridgeWireError, match="duplicate"):
        decode_tlvs(duplicate)
    with pytest.raises(BridgeWireError, match="unknown"):
        decode_tlvs(encode_tlvs([(999, b"x")]), allowed={int(FieldId.OPERATION)})
    with pytest.raises(BridgeWireError, match="truncated"):
        decode_tlvs(b"\x01\x00\x04\x00x")
    with pytest.raises(BridgeWireError, match="duplicate"):
        encode_tlvs([(FieldId.DATA, b"a"), (FieldId.DATA, b"b")])
    with pytest.raises(BridgeWireError, match="unknown bridge operation"):
        encode_request_body({"operation": "shell_escape", "command": "id"})


def test_resource_claims_are_atomic_and_fail_closed() -> None:
    resources = BridgeResourceManager()
    assert resources.claim("spi0", ["D22"]).pins == ("D22",)
    resources.claim("spi0", ["D22"])
    with pytest.raises(BridgeResourceConflict, match="spi0"):
        resources.claim("gpio", ["D22", "D23"])
    assert resources.conflicts() == ["D22:spi0"]
    with pytest.raises(ValueError, match="repeat"):
        resources.claim("gpio", ["D23", "D23"])
    resources.release("spi0")
    assert resources.claim("gpio", ["D22"]).owner == "gpio"


def test_named_profiles_require_strict_mode_and_redact_secrets(tmp_path: Path) -> None:
    profiles = tmp_path / "bridge-profiles.json"
    payload = {
        "wifi": {"lab": {"ssid": "fixture-net", "password": "secret123"}},
        "ble_passkeys": {"sensor": {"passkey": "123456"}},
    }
    profiles.write_text(json.dumps(payload), encoding="utf-8")
    profiles.chmod(0o644)
    with pytest.raises(PermissionError, match="0600"):
        load_bridge_profiles(profiles)
    profiles.chmod(0o600)
    loaded = load_bridge_profiles(profiles)
    serialized = loaded.model_dump_json()
    assert "secret123" not in serialized
    assert "123456" not in serialized
    assert loaded.wifi["lab"].password.get_secret_value() == "secret123"
    with pytest.raises(RuntimeError, match="BRIDGE_PROFILES"):
        load_bridge_profiles(None)

    pair = BlePairRequest(operation="ble_pair", passkey_profile="sensor")
    pair_fields = decode_tlvs(encode_request_body(pair, secrets={"passkey": "123456"}))
    assert pair_fields[int(FieldId.PASSKEY)] == b"123456"
    assert b"sensor" not in b"".join(pair_fields.values())


@pytest.mark.asyncio
async def test_binary_serial_and_bridge_backend_never_log_payloads(monkeypatch) -> None:
    serial = SerialBackend()
    monkeypatch.setattr(
        serial, "_exchange_binary_sync", lambda *args: b"response-secret"
    )
    result, raw = await serial.exchange_binary("/dev/tty-test", write=b"request-secret")
    assert result.ok and raw == b"response-secret"
    dumped = result.model_dump_json()
    assert "request-secret" not in dumped and "response-secret" not in dumped

    request_id = 0x10203040
    monkeypatch.setattr(
        "jlink_mcp_giga_protocol_bridge.backend.secrets.randbits",
        lambda _: request_id,
    )
    response_body = encode_tlvs(
        [
            (FieldId.STATUS, struct.pack("<H", 0)),
            (FieldId.RESPONSE_DATA, b"opaque-response"),
            (FieldId.METADATA_JSON, b'{"fixture":true}'),
            (FieldId.TIMESTAMP_US, struct.pack("<Q", 123)),
        ]
    )
    response_stream = encode_message(
        response_body, message_type=MessageType.RESPONSE, request_id=request_id
    )

    class FakeSerial:
        async def exchange_binary(self, port, **kwargs):
            assert port == "/dev/tty-test"
            assert kwargs["write"].endswith(b"\x00")
            request_sha256 = hashlib.sha256(kwargs["write"]).hexdigest()
            inherited = make_result(
                backend="usb-serial",
                parsed={"request_sha256": request_sha256},
            )
            inherited.command = [
                "serial-binary",
                port,
                f"sha256:{request_sha256}",
            ]
            return inherited, response_stream

    bridge = ProtocolBridgeBackend(FakeSerial())
    bridge_result = await bridge.request(
        "/dev/tty-test",
        {"operation": "get_status"},
        secrets_to_send={"password": "request-secret"},
    )
    assert bridge_result.ok
    assert bridge_result.parsed["bridge"]["data_base64"] == _b64(b"opaque-response")
    assert "opaque-response" not in bridge_result.model_dump_json()
    assert "request-secret" not in bridge_result.model_dump_json()

    assert "request_sha256" not in bridge_result.parsed
    assert "request_body_sha256" not in bridge_result.parsed
    assert not any("sha256" in item for item in bridge_result.command)
    error_body = encode_tlvs(
        [
            (FieldId.STATUS, struct.pack("<H", 9)),
            (FieldId.ERROR_MESSAGE, b"peer leaked a secret"),
        ]
    )
    error_stream = encode_message(
        error_body, message_type=MessageType.ERROR, request_id=request_id
    )

    class ErrorSerial:
        async def exchange_binary(self, port, **kwargs):
            return make_result(backend="usb-serial"), error_stream

    failed = await ProtocolBridgeBackend(ErrorSerial()).request(
        "/dev/tty-test", {"operation": "get_status"}, secrets_to_send={"password": "x"}
    )
    assert not failed.ok
    assert failed.stderr == "bridge operation failed"
    assert "peer leaked" not in failed.model_dump_json()


@pytest.mark.asyncio
async def test_bridge_backend_serializes_complete_exchange_per_port() -> None:
    class SerializedSerial:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def exchange_binary(self, port, **kwargs):
            frame = decode_stream(kwargs["write"])[0]
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            response = encode_message(
                encode_tlvs(
                    [
                        (FieldId.STATUS, struct.pack("<H", 0)),
                        (FieldId.TIMESTAMP_US, struct.pack("<Q", 123)),
                    ]
                ),
                message_type=MessageType.RESPONSE,
                request_id=frame.request_id,
            )
            return make_result(), response

    serial = SerializedSerial()
    backend = ProtocolBridgeBackend(serial)
    first, second = await asyncio.gather(
        backend.request("/dev/tty-shared", {"operation": "get_status"}),
        backend.request("/dev/tty-shared", {"operation": "get_status"}),
    )

    assert first.ok and second.ok
    assert serial.max_active == 1


@pytest.mark.asyncio
async def test_bridge_backend_timeout_cancellation_and_partial_response_mapping() -> (
    None
):
    class TimedOutSerial:
        async def exchange_binary(self, port, **kwargs):
            return make_result(return_code=None, timed_out=True), b"partial-response"

    timed_out = await ProtocolBridgeBackend(TimedOutSerial()).request(
        "/dev/tty-test", {"operation": "get_status"}
    )
    assert timed_out.timed_out and not timed_out.ok
    assert "wire_error" not in timed_out.parsed

    class CancelledSerial:
        async def exchange_binary(self, port, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ProtocolBridgeBackend(CancelledSerial()).request(
            "/dev/tty-test", {"operation": "get_status"}
        )

    class PartialSerial:
        async def exchange_binary(self, port, **kwargs):
            request_id = int(kwargs["write"][6:10].hex(), 16)
            response = encode_message(
                encode_tlvs([(FieldId.STATUS, b"\x00\x00")]),
                message_type=MessageType.RESPONSE,
                request_id=request_id,
            )
            return make_result(), response[:-1]

    partial = await ProtocolBridgeBackend(PartialSerial()).request(
        "/dev/tty-test", {"operation": "get_status"}
    )
    assert not partial.ok
    assert partial.parsed["wire_error"]["type"] == "BridgeWireError"
    assert partial.parsed["wire_error"]["message"] == (
        "bridge response ended with a partial COBS frame"
    )


def test_queue_overflow_and_result_serialization() -> None:
    response = decode_response_body(
        encode_tlvs(
            [
                (FieldId.STATUS, struct.pack("<H", 0)),
                (FieldId.RESPONSE_DATA, b"queued"),
                (FieldId.QUEUE_DEPTH, struct.pack("<H", 3)),
                (FieldId.OVERFLOW_COUNT, struct.pack("<I", 2)),
                (FieldId.TIMESTAMP_US, struct.pack("<Q", 123)),
            ]
        )
    )
    result = ProtocolBridgeService._result(
        "uart_receive",
        make_result(
            backend="giga-protocol-bridge",
            parsed={
                "bridge": {
                    **{key: value for key, value in response.items() if key != "data"},
                }
            },
        ),
    )
    dumped = result.model_dump(mode="json")
    assert dumped["data_base64"] == _b64(b"queued")
    assert dumped["byte_count"] == 6
    assert dumped["overflow"] is True
    assert dumped["metadata"] == {"queue_depth": 3, "overflow_count": 2}
