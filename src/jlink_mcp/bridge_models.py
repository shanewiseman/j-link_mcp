"""Typed public contracts for the Arduino GIGA universal protocol bridge."""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from .models import Artifact, CommandResult, DeviceSelector, utc_now

MAX_BRIDGE_PAYLOAD = 64 * 1024
MAX_APPLICATION_PAYLOAD = 64_000
MAX_TRANSPORT_FRAME = 4096
BRIDGE_WIRE_VERSION = 1
BRIDGE_FIRMWARE_VERSION = "1.0.0"

# Arduino labels exposed for unowned GPIO and caller-selected chip selects.
# Fixed bus pins and D86-D102 (LED, USB host, radio, BOOT0, internal I2C) are
# intentionally absent.
SAFE_GPIO_PINS = tuple(
    [f"D{pin}" for pin in range(2, 8)]
    + [f"D{pin}" for pin in range(22, 86)]
)
_SAFE_GPIO_SET = frozenset(SAFE_GPIO_PINS)
_PIN_RE = re.compile(r"^D([0-9]|[1-9][0-9]|10[0-2])$")


class BridgeModel(BaseModel):
    """Fail-closed base for every bridge-facing contract."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


def decode_canonical_base64(value: str, *, maximum: int = MAX_BRIDGE_PAYLOAD) -> bytes:
    """Decode canonical RFC 4648 base64 and enforce the bridge payload bound."""

    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("payload must be canonical RFC 4648 base64") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("payload must use canonical padded RFC 4648 base64")
    if len(decoded) > maximum:
        raise ValueError(f"decoded payload exceeds {maximum} bytes")
    return decoded


def encode_canonical_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def validate_safe_pin(value: str) -> str:
    label = value.strip().upper()
    if not _PIN_RE.fullmatch(label) or label not in _SAFE_GPIO_SET:
        raise ValueError(
            "pin is protected, internal, bus-owned, or not an exposed safe Arduino label"
        )
    return label


class BridgeProtocol(StrEnum):
    SPI = "spi"
    I2C = "i2c"
    UART = "uart"
    CAN = "can"
    USB = "usb"
    WIFI = "wifi"
    BLE = "ble"
    GPIO = "gpio"


class BitOrder(StrEnum):
    MSB_FIRST = "msb_first"
    LSB_FIRST = "lsb_first"


class UartParity(StrEnum):
    NONE = "none"
    EVEN = "even"
    ODD = "odd"


class GpioMode(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class GpioPull(StrEnum):
    NONE = "none"
    UP = "up"
    DOWN = "down"


class GpioEdge(StrEnum):
    NONE = "none"
    RISING = "rising"
    FALLING = "falling"
    CHANGE = "change"


class SocketProtocol(StrEnum):
    TCP = "tcp"
    UDP = "udp"


class UsbTransferType(StrEnum):
    CONTROL = "control"
    BULK = "bulk"
    INTERRUPT = "interrupt"


class BleWriteMode(StrEnum):
    WITH_RESPONSE = "with_response"
    WITHOUT_RESPONSE = "without_response"


class PayloadRequest(BridgeModel):
    data_base64: str = Field(
        default="",
        description="Canonical padded RFC 4648 base64; decoded bytes are uninterpreted.",
    )

    @field_validator("data_base64")
    @classmethod
    def validate_payload(cls, value: str) -> str:
        decode_canonical_base64(value, maximum=MAX_APPLICATION_PAYLOAD)
        return value


class UartConfigureRequest(BridgeModel):
    operation: Literal["uart_configure"]
    port: int = Field(ge=0, le=3)
    baudrate: int = Field(ge=50, le=4_000_000)
    data_bits: Literal[7, 8] = 8
    parity: UartParity = UartParity.NONE
    stop_bits: Literal[1, 2] = 1


class CanConfigureRequest(BridgeModel):
    operation: Literal["can_configure"]
    bus: int = Field(ge=0, le=1)
    bitrate: Literal[125000, 250000, 500000, 1000000]


class UsbEnumerateRequest(BridgeModel):
    operation: Literal["usb_enumerate"]


class UsbSelectRequest(BridgeModel):
    operation: Literal["usb_select"]
    vendor_id: int = Field(ge=0, le=0xFFFF)
    product_id: int = Field(ge=0, le=0xFFFF)
    serial: str | None = Field(default=None, min_length=1, max_length=126)
    interface_number: int | None = Field(default=None, ge=0, le=255)


class UsbResetRequest(BridgeModel):
    operation: Literal["usb_reset"]


class UsbReleaseRequest(BridgeModel):
    operation: Literal["usb_release"]


class WifiConnectRequest(BridgeModel):
    operation: Literal["wifi_connect"]
    profile: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


class WifiDisconnectRequest(BridgeModel):
    operation: Literal["wifi_disconnect"]


class WifiSocketOpenRequest(BridgeModel):
    operation: Literal["wifi_socket_open"]
    protocol: SocketProtocol
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    local_port: int | None = Field(default=None, ge=1, le=65535)


class WifiSocketCloseRequest(BridgeModel):
    operation: Literal["wifi_socket_close"]
    socket_id: int = Field(ge=0, le=7)


class BleScanRequest(BridgeModel):
    operation: Literal["ble_scan"]
    duration_ms: int = Field(default=3000, ge=100, le=30000)
    service_uuid: str | None = Field(default=None, min_length=4, max_length=36)


class BleConnectRequest(BridgeModel):
    operation: Literal["ble_connect"]
    address: str = Field(
        min_length=17,
        max_length=17,
        pattern=r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$",
    )


class BleDisconnectRequest(BridgeModel):
    operation: Literal["ble_disconnect"]


class BlePairRequest(BridgeModel):
    operation: Literal["ble_pair"]
    passkey_profile: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"
    )


class BleDiscoverRequest(BridgeModel):
    operation: Literal["ble_discover"]
    service_uuid: str | None = Field(default=None, min_length=4, max_length=36)


class BleSubscribeRequest(BridgeModel):
    operation: Literal["ble_subscribe"]
    service_uuid: str = Field(min_length=4, max_length=36)
    characteristic_uuid: str = Field(min_length=4, max_length=36)
    enabled: bool = True


class GpioConfigureRequest(BridgeModel):
    operation: Literal["gpio_configure"]
    pin: str
    mode: GpioMode
    pull: GpioPull = GpioPull.NONE
    initial_value: bool | None = None

    @field_validator("pin")
    @classmethod
    def safe_pin(cls, value: str) -> str:
        return validate_safe_pin(value)

    @model_validator(mode="after")
    def validate_configuration(self) -> "GpioConfigureRequest":
        if self.mode == GpioMode.OUTPUT and self.pull != GpioPull.NONE:
            raise ValueError("GPIO output does not accept a pull mode")
        if self.mode == GpioMode.INPUT and self.initial_value is not None:
            raise ValueError("GPIO input does not accept an initial value")
        return self


class GpioWatchRequest(BridgeModel):
    operation: Literal["gpio_watch"]
    pin: str
    edge: GpioEdge

    @field_validator("pin")
    @classmethod
    def safe_pin(cls, value: str) -> str:
        return validate_safe_pin(value)


ProtocolBridgeControlRequest = Annotated[
    UartConfigureRequest
    | CanConfigureRequest
    | UsbEnumerateRequest
    | UsbSelectRequest
    | UsbResetRequest
    | UsbReleaseRequest
    | WifiConnectRequest
    | WifiDisconnectRequest
    | WifiSocketOpenRequest
    | WifiSocketCloseRequest
    | BleScanRequest
    | BleConnectRequest
    | BleDisconnectRequest
    | BlePairRequest
    | BleDiscoverRequest
    | BleSubscribeRequest
    | GpioConfigureRequest
    | GpioWatchRequest,
    Field(discriminator="operation"),
]


class SpiExchangeRequest(PayloadRequest):
    operation: Literal["spi_exchange"]
    bus: int = Field(ge=0, le=1)
    chip_select: str
    clock_hz: int = Field(default=1_000_000, ge=1_000, le=50_000_000)
    mode: Literal[0, 1, 2, 3] = 0
    bit_order: BitOrder = BitOrder.MSB_FIRST
    fill_byte: int = Field(default=0xFF, ge=0, le=255)
    read_length: int = Field(default=0, ge=0, le=MAX_APPLICATION_PAYLOAD)

    @field_validator("chip_select")
    @classmethod
    def safe_chip_select(cls, value: str) -> str:
        return validate_safe_pin(value)

    @model_validator(mode="after")
    def limit_clocks(self) -> "SpiExchangeRequest":
        write_size = len(decode_canonical_base64(self.data_base64))
        if max(write_size, self.read_length) > MAX_APPLICATION_PAYLOAD:
            raise ValueError("SPI transfer exceeds the 64,000-byte request limit")
        return self


class I2cExchangeRequest(PayloadRequest):
    operation: Literal["i2c_exchange"]
    bus: Literal[0, 1]
    address: int = Field(ge=0x08, le=0x77)
    read_length: int = Field(default=0, ge=0, le=32)

    @model_validator(mode="after")
    def limit_i2c_transaction(self) -> "I2cExchangeRequest":
        if len(decode_canonical_base64(self.data_base64, maximum=32)) > 32:
            raise ValueError("I2C writes are limited to the 32-byte Wire buffer")
        return self
    repeated_start: bool = True
    clock_hz: Literal[100000, 400000] = 100000


class UartWriteRequest(PayloadRequest):
    operation: Literal["uart_write"]
    port: int = Field(ge=0, le=3)


class CanSendRequest(PayloadRequest):
    operation: Literal["can_send"]
    bus: int = Field(ge=0, le=1)
    arbitration_id: int = Field(ge=0, le=0x1FFFFFFF)
    extended: bool = False

    @model_validator(mode="after")
    def validate_can_frame(self) -> "CanSendRequest":
        payload = decode_canonical_base64(self.data_base64, maximum=8)
        if not self.extended and self.arbitration_id > 0x7FF:
            raise ValueError("standard CAN identifiers must be at most 0x7FF")
        if len(payload) > 8:
            raise ValueError("classic CAN payloads are limited to 8 bytes")
        return self


class UsbTransferRequest(PayloadRequest):
    operation: Literal["usb_transfer"]
    transfer_type: UsbTransferType
    endpoint: int = Field(default=0, ge=0, le=0x8F)
    read_length: int = Field(default=0, ge=0, le=MAX_APPLICATION_PAYLOAD)
    request_type: int | None = Field(default=None, ge=0, le=255)
    request: int | None = Field(default=None, ge=0, le=255)
    value: int | None = Field(default=None, ge=0, le=0xFFFF)
    index: int | None = Field(default=None, ge=0, le=0xFFFF)

    @model_validator(mode="after")
    def validate_usb_transfer(self) -> "UsbTransferRequest":
        control_fields = (self.request_type, self.request, self.value, self.index)
        if self.transfer_type == UsbTransferType.CONTROL:
            if any(value is None for value in control_fields):
                raise ValueError("control USB transfers require setup packet fields")
            if self.endpoint != 0:
                raise ValueError("control transfers use endpoint zero")
        elif any(value is not None for value in control_fields):
            raise ValueError("USB setup packet fields are control-transfer only")
        if self.transfer_type == UsbTransferType.INTERRUPT and self.read_length > 4096:
            raise ValueError("interrupt transfers are limited to 4096 bytes")
        return self


class WifiSendRequest(PayloadRequest):
    operation: Literal["wifi_send"]
    socket_id: int = Field(ge=0, le=7)


class BleReadRequest(BridgeModel):
    operation: Literal["ble_read"]
    service_uuid: str = Field(min_length=4, max_length=36)
    characteristic_uuid: str = Field(min_length=4, max_length=36)


class BleWriteRequest(PayloadRequest):
    operation: Literal["ble_write"]
    service_uuid: str = Field(min_length=4, max_length=36)
    characteristic_uuid: str = Field(min_length=4, max_length=36)
    write_mode: BleWriteMode = BleWriteMode.WITH_RESPONSE


class GpioReadRequest(BridgeModel):
    operation: Literal["gpio_read"]
    pin: str

    @field_validator("pin")
    @classmethod
    def safe_pin(cls, value: str) -> str:
        return validate_safe_pin(value)


class GpioWriteRequest(BridgeModel):
    operation: Literal["gpio_write"]
    pin: str
    value: bool

    @field_validator("pin")
    @classmethod
    def safe_pin(cls, value: str) -> str:
        return validate_safe_pin(value)


class GpioPulseRequest(BridgeModel):
    operation: Literal["gpio_pulse"]
    pin: str
    value: bool
    duration_us: int = Field(ge=1, le=1_000_000)

    @field_validator("pin")
    @classmethod
    def safe_pin(cls, value: str) -> str:
        return validate_safe_pin(value)


ProtocolBridgeExchangeRequest = Annotated[
    SpiExchangeRequest
    | I2cExchangeRequest
    | UartWriteRequest
    | CanSendRequest
    | UsbTransferRequest
    | WifiSendRequest
    | BleReadRequest
    | BleWriteRequest
    | GpioReadRequest
    | GpioWriteRequest
    | GpioPulseRequest,
    Field(discriminator="operation"),
]


class ProtocolBridgeReceiveRequest(BridgeModel):
    protocol: Literal["uart", "can", "usb", "wifi", "ble", "gpio"]
    channel: int = Field(default=0, ge=0, le=255)
    limit: int = Field(default=4096, ge=1, le=MAX_APPLICATION_PAYLOAD)
    drain: bool = True
    timeout_ms: int = Field(default=0, ge=0, le=300000)


class ProtocolBridgeStatus(BridgeModel):
    firmware_version: str
    wire_version: int
    build_id: str
    source_sha256: str
    supported_interfaces: list[BridgeProtocol]
    safe_pins: list[str]
    transfer_limits: dict[str, int]
    connections: dict[str, Any] = Field(default_factory=dict)
    queue_depths: dict[str, int] = Field(default_factory=dict)
    overflow_counts: dict[str, int] = Field(default_factory=dict)
    active_resource_conflicts: list[str] = Field(default_factory=list)
    command: CommandResult


class ProtocolBridgeResult(BridgeModel):
    protocol: BridgeProtocol
    operation: str
    data_base64: str = ""
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hex_preview: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)
    overflow: bool = False
    command: CommandResult

    @field_validator("data_base64")
    @classmethod
    def canonical_response_payload(cls, value: str) -> str:
        decode_canonical_base64(value)
        return value


class ProtocolBridgeDeployResult(BridgeModel):
    selector: DeviceSelector
    preflight: dict[str, Any]
    backup: Artifact
    firmware: Artifact
    flash: CommandResult
    handshake: ProtocolBridgeStatus

    @property
    def ok(self) -> bool:
        return self.flash.ok and self.handshake.wire_version == BRIDGE_WIRE_VERSION


class ProtocolBridgeReleaseResult(BridgeModel):
    source_sha256: str
    build_directory: str
    command: CommandResult
    artifacts: list[Artifact]
    checked_in_hex: str
    reproducible: bool


class WifiCredentialProfile(BridgeModel):
    ssid: SecretStr = Field(min_length=1, max_length=32)
    password: SecretStr = Field(min_length=8, max_length=63)


class BlePasskeyProfile(BridgeModel):
    passkey: SecretStr

    @field_validator("passkey")
    @classmethod
    def six_digits(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"[0-9]{6}", value.get_secret_value()):
            raise ValueError("BLE passkey must contain exactly six digits")
        return value


class ProtocolBridgeProfiles(BridgeModel):
    wifi: dict[str, WifiCredentialProfile] = Field(default_factory=dict)
    ble_passkeys: dict[str, BlePasskeyProfile] = Field(default_factory=dict)
