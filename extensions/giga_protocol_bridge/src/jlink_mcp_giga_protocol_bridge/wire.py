"""Versioned COBS, segmented-frame, CRC-32, and typed-TLV bridge codec."""

from __future__ import annotations

import base64
import json
import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from pydantic import BaseModel

from .models import (
    BRIDGE_WIRE_VERSION,
    MAX_BRIDGE_PAYLOAD,
    MAX_TRANSPORT_FRAME,
    decode_canonical_base64,
)

MAGIC = b"JLPB"
_HEADER_WITHOUT_CRC = struct.Struct("<4sBBIHHH")
_HEADER = struct.Struct("<4sBBIHHHI")
_TLV = struct.Struct("<HH")
MAX_SEGMENT_PAYLOAD = MAX_TRANSPORT_FRAME - _HEADER.size
MAX_COBS_FRAME = MAX_TRANSPORT_FRAME + (MAX_TRANSPORT_FRAME // 254) + 2


class BridgeWireError(ValueError):
    pass


class BridgeCrcError(BridgeWireError):
    pass


class BridgeSequenceError(BridgeWireError):
    pass


class MessageType(IntEnum):
    REQUEST = 1
    RESPONSE = 2
    ERROR = 3


class Operation(IntEnum):
    GET_STATUS = 1
    RECEIVE = 2
    UART_CONFIGURE = 10
    CAN_CONFIGURE = 11
    USB_ENUMERATE = 12
    USB_SELECT = 13
    USB_RESET = 14
    USB_RELEASE = 15
    WIFI_CONNECT = 16
    WIFI_DISCONNECT = 17
    WIFI_SOCKET_OPEN = 18
    WIFI_SOCKET_CLOSE = 19
    BLE_SCAN = 20
    BLE_CONNECT = 21
    BLE_DISCONNECT = 22
    BLE_PAIR = 23
    BLE_DISCOVER = 24
    BLE_SUBSCRIBE = 25
    GPIO_CONFIGURE = 26
    GPIO_WATCH = 27
    SPI_EXCHANGE = 40
    I2C_EXCHANGE = 41
    UART_WRITE = 42
    CAN_SEND = 43
    USB_TRANSFER = 44
    WIFI_SEND = 45
    BLE_READ = 46
    BLE_WRITE = 47
    GPIO_READ = 48
    GPIO_WRITE = 49
    GPIO_PULSE = 50


OPERATIONS = {item.name.lower(): item for item in Operation}


class FieldId(IntEnum):
    OPERATION = 1
    PROTOCOL = 2
    BUS = 3
    UART_PORT = 4
    DATA = 5
    READ_LENGTH = 6
    CLOCK_HZ = 7
    MODE = 8
    BIT_ORDER = 9
    CHIP_SELECT = 10
    FILL_BYTE = 11
    I2C_ADDRESS = 12
    REPEATED_START = 13
    BAUDRATE = 14
    DATA_BITS = 15
    PARITY = 16
    STOP_BITS = 17
    CAN_BITRATE = 18
    CAN_ID = 19
    CAN_EXTENDED = 20
    VID = 21
    PID = 22
    SERIAL = 23
    USB_INTERFACE = 24
    ENDPOINT = 25
    TRANSFER_TYPE = 26
    REQUEST_TYPE = 27
    REQUEST = 28
    VALUE = 29
    INDEX = 30
    PROFILE = 31
    HOST = 32
    NETWORK_PORT = 33
    SOCKET_ID = 34
    SOCKET_PROTOCOL = 35
    BLE_ADDRESS = 36
    SERVICE_UUID = 37
    CHARACTERISTIC_UUID = 38
    WRITE_MODE = 39
    PIN = 40
    GPIO_MODE = 41
    GPIO_VALUE = 42
    PULL = 43
    EDGE = 44
    DURATION_US = 45
    LIMIT = 46
    DRAIN = 47
    TIMEOUT_MS = 48
    CHANNEL = 49
    SCAN_DURATION_MS = 50
    PASSKEY = 51
    SSID = 52
    PASSWORD = 53
    LOCAL_PORT = 54
    ENABLED = 55
    INITIAL_VALUE = 56
    STATUS = 0x8001
    RESPONSE_DATA = 0x8002
    METADATA_JSON = 0x8003
    QUEUE_DEPTH = 0x8004
    OVERFLOW_COUNT = 0x8005
    TIMESTAMP_US = 0x8006
    ERROR_MESSAGE = 0x8007


FIELD_NAMES: dict[str, FieldId] = {
    "protocol": FieldId.PROTOCOL,
    "bus": FieldId.BUS,
    "port": FieldId.UART_PORT,
    "data_base64": FieldId.DATA,
    "read_length": FieldId.READ_LENGTH,
    "clock_hz": FieldId.CLOCK_HZ,
    "mode": FieldId.MODE,
    "bit_order": FieldId.BIT_ORDER,
    "chip_select": FieldId.CHIP_SELECT,
    "fill_byte": FieldId.FILL_BYTE,
    "address": FieldId.I2C_ADDRESS,
    "repeated_start": FieldId.REPEATED_START,
    "baudrate": FieldId.BAUDRATE,
    "data_bits": FieldId.DATA_BITS,
    "parity": FieldId.PARITY,
    "stop_bits": FieldId.STOP_BITS,
    "bitrate": FieldId.CAN_BITRATE,
    "arbitration_id": FieldId.CAN_ID,
    "extended": FieldId.CAN_EXTENDED,
    "vendor_id": FieldId.VID,
    "product_id": FieldId.PID,
    "serial": FieldId.SERIAL,
    "interface_number": FieldId.USB_INTERFACE,
    "endpoint": FieldId.ENDPOINT,
    "transfer_type": FieldId.TRANSFER_TYPE,
    "request_type": FieldId.REQUEST_TYPE,
    "request": FieldId.REQUEST,
    "value": FieldId.VALUE,
    "index": FieldId.INDEX,
    "profile": FieldId.PROFILE,
    "host": FieldId.HOST,
    "socket_id": FieldId.SOCKET_ID,
    "write_mode": FieldId.WRITE_MODE,
    "service_uuid": FieldId.SERVICE_UUID,
    "characteristic_uuid": FieldId.CHARACTERISTIC_UUID,
    "pin": FieldId.PIN,
    "pull": FieldId.PULL,
    "edge": FieldId.EDGE,
    "duration_us": FieldId.DURATION_US,
    "limit": FieldId.LIMIT,
    "drain": FieldId.DRAIN,
    "timeout_ms": FieldId.TIMEOUT_MS,
    "channel": FieldId.CHANNEL,
    "duration_ms": FieldId.SCAN_DURATION_MS,
    "enabled": FieldId.ENABLED,
    "initial_value": FieldId.INITIAL_VALUE,
}

_ENUM_VALUES: dict[str, dict[str, int]] = {
    "protocol": {
        "spi": 0,
        "i2c": 1,
        "uart": 2,
        "can": 3,
        "usb": 4,
        "wifi": 5,
        "ble": 6,
        "gpio": 7,
    },
    "bit_order": {"msb_first": 0, "lsb_first": 1},
    "parity": {"none": 0, "even": 1, "odd": 2},
    "transfer_type": {"control": 0, "bulk": 1, "interrupt": 2},
    "write_mode": {"with_response": 0, "without_response": 1},
    "pull": {"none": 0, "up": 1, "down": 2},
    "edge": {"none": 0, "rising": 1, "falling": 2, "change": 3},
    "mode": {"input": 0, "output": 1},
    "protocol_socket": {"tcp": 0, "udp": 1},
}

_U8_FIELDS = {
    "bus",
    "port",
    "mode",
    "bit_order",
    "fill_byte",
    "address",
    "data_bits",
    "parity",
    "stop_bits",
    "extended",
    "interface_number",
    "endpoint",
    "transfer_type",
    "request_type",
    "request",
    "socket_id",
    "write_mode",
    "pull",
    "edge",
    "channel",
    "drain",
    "enabled",
    "initial_value",
    "value_gpio",
    "repeated_start",
    "protocol",
}
_U16_FIELDS = {
    "vendor_id",
    "product_id",
    "value",
    "index",
    "port_network",
    "local_port",
    "limit",
    "pin",
    "chip_select",
}
_U32_FIELDS = {
    "read_length",
    "clock_hz",
    "baudrate",
    "bitrate",
    "arbitration_id",
    "duration_us",
    "timeout_ms",
    "duration_ms",
}


@dataclass(frozen=True, slots=True)
class WireFrame:
    message_type: MessageType
    request_id: int
    segment_index: int
    segment_count: int
    payload: bytes
    wire_version: int = BRIDGE_WIRE_VERSION


def cobs_encode(data: bytes) -> bytes:
    output = bytearray(b"\x00")
    code_index = 0
    code = 1
    for value in data:
        if value == 0:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1
        else:
            output.append(value)
            code += 1
            if code == 0xFF:
                output[code_index] = code
                code_index = len(output)
                output.append(0)
                code = 1
    output[code_index] = code
    return bytes(output)


def cobs_decode(data: bytes) -> bytes:
    if not data or b"\x00" in data:
        raise BridgeWireError("COBS frame is empty or contains a delimiter")
    output = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        if code == 0:
            raise BridgeWireError("COBS code byte cannot be zero")
        index += 1
        end = index + code - 1
        if end > len(data):
            raise BridgeWireError("COBS code exceeds encoded frame")
        output.extend(data[index:end])
        index = end
        if code != 0xFF and index < len(data):
            output.append(0)
    return bytes(output)


def encode_frame(frame: WireFrame) -> bytes:
    if frame.wire_version != BRIDGE_WIRE_VERSION:
        raise BridgeWireError("unsupported bridge wire version")
    if not 0 <= frame.request_id <= 0xFFFFFFFF:
        raise BridgeWireError("request ID is outside uint32")
    if not 0 <= frame.segment_index < frame.segment_count <= 0xFFFF:
        raise BridgeSequenceError("invalid segment index/count")
    if len(frame.payload) > MAX_SEGMENT_PAYLOAD:
        raise BridgeWireError("segment exceeds decoded transport frame limit")
    prefix = _HEADER_WITHOUT_CRC.pack(
        MAGIC,
        frame.wire_version,
        int(frame.message_type),
        frame.request_id,
        frame.segment_index,
        frame.segment_count,
        len(frame.payload),
    )
    crc = zlib.crc32(prefix + frame.payload) & 0xFFFFFFFF
    decoded = prefix + struct.pack("<I", crc) + frame.payload
    if len(decoded) > MAX_TRANSPORT_FRAME:
        raise BridgeWireError("decoded frame exceeds transport limit")
    return cobs_encode(decoded) + b"\x00"


def decode_frame(encoded: bytes) -> WireFrame:
    if encoded.endswith(b"\x00"):
        encoded = encoded[:-1]
    if len(encoded) > MAX_COBS_FRAME:
        raise BridgeWireError("encoded frame exceeds transport limit")
    decoded = cobs_decode(encoded)
    if len(decoded) < _HEADER.size:
        raise BridgeWireError("decoded frame is shorter than its header")
    (
        magic,
        version,
        message_type,
        request_id,
        segment_index,
        segment_count,
        payload_length,
        expected_crc,
    ) = _HEADER.unpack_from(decoded)
    if magic != MAGIC:
        raise BridgeWireError("bridge frame magic mismatch")
    if version != BRIDGE_WIRE_VERSION:
        raise BridgeWireError(f"unsupported bridge wire version: {version}")
    if len(decoded) != _HEADER.size + payload_length:
        raise BridgeWireError("bridge payload length mismatch")
    if not 0 <= segment_index < segment_count:
        raise BridgeSequenceError("invalid segment index/count")
    payload = decoded[_HEADER.size :]
    actual_crc = zlib.crc32(decoded[: _HEADER_WITHOUT_CRC.size] + payload) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise BridgeCrcError(
            f"bridge CRC mismatch: expected 0x{expected_crc:08X}, got 0x{actual_crc:08X}"
        )
    try:
        kind = MessageType(message_type)
    except ValueError as exc:
        raise BridgeWireError(f"unknown bridge message type: {message_type}") from exc
    return WireFrame(
        message_type=kind,
        request_id=request_id,
        segment_index=segment_index,
        segment_count=segment_count,
        payload=payload,
        wire_version=version,
    )


def encode_message(
    payload: bytes, *, message_type: MessageType, request_id: int
) -> bytes:
    if len(payload) > MAX_BRIDGE_PAYLOAD:
        raise BridgeWireError("message exceeds the 64 KiB assembly limit")
    chunks = [
        payload[offset : offset + MAX_SEGMENT_PAYLOAD]
        for offset in range(0, len(payload), MAX_SEGMENT_PAYLOAD)
    ] or [b""]
    return b"".join(
        encode_frame(
            WireFrame(
                message_type=message_type,
                request_id=request_id,
                segment_index=index,
                segment_count=len(chunks),
                payload=chunk,
            )
        )
        for index, chunk in enumerate(chunks)
    )


def decode_stream(data: bytes) -> list[WireFrame]:
    if data and not data.endswith(b"\x00"):
        raise BridgeWireError("bridge response ended with a partial COBS frame")
    parts = data.split(b"\x00")
    return [decode_frame(part) for part in parts if part]


def reassemble_frames(
    frames: list[WireFrame],
    *,
    request_id: int | None = None,
    message_type: MessageType | None = None,
) -> bytes:
    if not frames:
        raise BridgeSequenceError("response contained no complete frames")
    first = frames[0]
    expected_request = first.request_id if request_id is None else request_id
    expected_type = first.message_type if message_type is None else message_type
    count = first.segment_count
    if len(frames) != count:
        raise BridgeSequenceError(
            f"response has {len(frames)} of {count} required segments"
        )
    for index, frame in enumerate(frames):
        if (
            frame.request_id != expected_request
            or frame.message_type != expected_type
            or frame.segment_count != count
            or frame.segment_index != index
        ):
            raise BridgeSequenceError("stale, mismatched, or out-of-order response")
    payload = b"".join(frame.payload for frame in frames)
    if len(payload) > MAX_BRIDGE_PAYLOAD:
        raise BridgeSequenceError("assembled response exceeds 64 KiB")
    return payload


def encode_tlvs(items: list[tuple[int | FieldId, bytes]]) -> bytes:
    output = bytearray()
    seen: set[int] = set()
    for raw_kind, value in items:
        kind = int(raw_kind)
        if kind in seen:
            raise BridgeWireError(f"duplicate TLV type: {kind}")
        if not 0 <= kind <= 0xFFFF or len(value) > 0xFFFF:
            raise BridgeWireError("TLV type or value is outside uint16 bounds")
        seen.add(kind)
        output.extend(_TLV.pack(kind, len(value)))
        output.extend(value)
    if len(output) > MAX_BRIDGE_PAYLOAD:
        raise BridgeWireError("TLV body exceeds 64 KiB")
    return bytes(output)


def decode_tlvs(
    payload: bytes, *, allowed: set[int] | None = None
) -> dict[int, bytes]:
    result: dict[int, bytes] = {}
    offset = 0
    while offset < len(payload):
        if len(payload) - offset < _TLV.size:
            raise BridgeWireError("truncated TLV header")
        kind, length = _TLV.unpack_from(payload, offset)
        offset += _TLV.size
        end = offset + length
        if end > len(payload):
            raise BridgeWireError("truncated TLV value")
        if kind in result:
            raise BridgeWireError(f"duplicate TLV type: {kind}")
        if allowed is not None and kind not in allowed:
            raise BridgeWireError(f"unknown TLV type: {kind}")
        result[kind] = payload[offset:end]
        offset = end
    return result


def _pin_number(value: str) -> int:
    if not value.startswith("D") or not value[1:].isdigit():
        raise BridgeWireError(f"invalid Arduino pin label: {value}")
    return int(value[1:])


def _scalar(field: str, value: Any) -> bytes:
    if field in {"pin", "chip_select"}:
        value = _pin_number(value)
    enum_map_name = "protocol_socket" if field == "protocol" and value in {"tcp", "udp"} else field
    if isinstance(value, str) and (mapping := _ENUM_VALUES.get(enum_map_name)):
        try:
            value = mapping[value]
        except KeyError as exc:
            raise BridgeWireError(f"unsupported {field} value: {value}") from exc
    if isinstance(value, bool):
        return bytes([int(value)])
    if field in _U8_FIELDS:
        return struct.pack("<B", value)
    if field in _U16_FIELDS:
        return struct.pack("<H", value)
    if field in _U32_FIELDS:
        return struct.pack("<I", value)
    if isinstance(value, int):
        if value < 0 or value > 0xFFFFFFFF:
            raise BridgeWireError(f"{field} integer is outside uint32")
        return struct.pack("<I", value)
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) > 1024:
            raise BridgeWireError(f"{field} string is too long")
        return encoded
    raise BridgeWireError(f"unsupported wire field type for {field}")


def encode_request_body(
    request: BaseModel | dict[str, Any],
    *,
    operation: str | None = None,
    secrets: dict[str, str] | None = None,
) -> bytes:
    values = (
        request.model_dump(mode="json", exclude_none=True)
        if isinstance(request, BaseModel)
        else dict(request)
    )
    operation_name = operation or values.pop("operation", None)
    if operation_name is None:
        operation_name = "receive" if "protocol" in values else "get_status"
    try:
        operation_code = OPERATIONS[operation_name]
    except KeyError as exc:
        raise BridgeWireError(f"unknown bridge operation: {operation_name}") from exc
    items: list[tuple[int | FieldId, bytes]] = [
        (FieldId.OPERATION, struct.pack("<H", operation_code))
    ]
    if operation_name == "wifi_socket_open":
        values["port_network"] = values.pop("port")
    if operation_name in {"gpio_write", "gpio_pulse"}:
        values["value_gpio"] = values.pop("value")
    if operation_name == "gpio_configure":
        values["mode"] = values.pop("mode")
    if secrets:
        values.update(secrets)
    for name, value in values.items():
        if name == "passkey_profile":
            # Profile names are audited but only the resolved passkey is sent.
            continue
        field_name = name
        if name == "port_network":
            field_id = FieldId.NETWORK_PORT
        elif name == "value_gpio":
            field_id = FieldId.GPIO_VALUE
        elif name == "mode" and operation_name == "gpio_configure":
            field_id = FieldId.GPIO_MODE
        elif name == "address" and operation_name == "ble_connect":
            field_id = FieldId.BLE_ADDRESS
        elif name == "protocol" and operation_name == "wifi_socket_open":
            field_id = FieldId.SOCKET_PROTOCOL
        elif name in {"ssid", "password", "passkey"}:
            field_id = FieldId[name.upper()]
        else:
            try:
                field_id = FIELD_NAMES[name]
            except KeyError as exc:
                raise BridgeWireError(f"wire field is not assigned: {name}") from exc
        if name == "data_base64":
            encoded = decode_canonical_base64(value)
        else:
            encoded = _scalar(field_name, value)
        items.append((field_id, encoded))
    return encode_tlvs(items)


def decode_response_body(payload: bytes) -> dict[str, Any]:
    allowed = {
        int(FieldId.STATUS),
        int(FieldId.RESPONSE_DATA),
        int(FieldId.METADATA_JSON),
        int(FieldId.QUEUE_DEPTH),
        int(FieldId.OVERFLOW_COUNT),
        int(FieldId.TIMESTAMP_US),
        int(FieldId.ERROR_MESSAGE),
    }
    values = decode_tlvs(payload, allowed=allowed)
    if int(FieldId.STATUS) not in values:
        raise BridgeWireError("response has no status TLV")
    status_raw = values[int(FieldId.STATUS)]
    if len(status_raw) != 2:
        raise BridgeWireError("response status is not uint16")
    status = struct.unpack("<H", status_raw)[0]
    data = values.get(int(FieldId.RESPONSE_DATA), b"")
    metadata_raw = values.get(int(FieldId.METADATA_JSON), b"{}")
    try:
        metadata = json.loads(metadata_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeWireError("response metadata is not valid UTF-8 JSON") from exc
    if not isinstance(metadata, dict):
        raise BridgeWireError("response metadata must be a JSON object")

    def integer(kind: FieldId, width: int) -> int:
        raw = values.get(int(kind))
        if raw is None:
            return 0
        if len(raw) != width:
            raise BridgeWireError(f"{kind.name} has an invalid width")
        return int.from_bytes(raw, "little")

    error_raw = values.get(int(FieldId.ERROR_MESSAGE), b"")
    try:
        error = error_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BridgeWireError("firmware error text is not UTF-8") from exc
    return {
        "status": status,
        "data": data,
        "data_base64": base64.b64encode(data).decode("ascii"),
        "metadata": metadata,
        "queue_depth": integer(FieldId.QUEUE_DEPTH, 2),
        "overflow_count": integer(FieldId.OVERFLOW_COUNT, 4),
        "timestamp_us": integer(FieldId.TIMESTAMP_US, 8),
        "error": error,
    }
