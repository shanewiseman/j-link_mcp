# GIGA universal protocol bridge

The protocol bridge is a dedicated Arduino GIGA R1 WiFi M7 image. It accepts
versioned binary requests over the USB-C CDC control plane and acts as an SPI
or I²C controller, UART or CAN endpoint, USB-A host, Wi-Fi station, BLE central,
or digital GPIO controller. Payload bytes are opaque; the bridge does not parse
application protocols.

## Safe deployment and recovery

Begin with `get_capabilities`, `dependency_doctor`, and `hardware_preflight`.
`deploy_protocol_bridge` then verifies the checked-in release manifest, backs
up all 2 MiB from `0x08000000`, flashes and verifies the M7 HEX, resets the
board, and requires a firmware/wire/source-hash handshake. It refuses to flash
when the backup fails. The returned `backup` artifact is the recovery image.

To restore the original target, pass that artifact path, address `0x08000000`,
and its SHA-256 to `restore_flash_backup`. Restoration performs a complete
readback/hash comparison and returns the target to run. Keep the backup and its
audit record until the restored hash has been independently verified.

`build_protocol_bridge_release` compiles with Arduino CLI 1.5.1,
`arduino:mbed_giga@4.6.0`, a fixed release epoch, and the pinned USB/BLE
libraries. ELF, BIN, and map files remain in managed `state/`; the repository
contains only the flashable HEX, manifest, and `SHA256SUMS`. The normal release
check requires the rebuilt HEX to match the checked-in image byte-for-byte.

## Wiring and pin ownership

The GIGA must be powered through USB and the J-Link wired to VTref, GND, SWDIO,
SWCLK, and nRESET. USB-C remains the bridge control plane. Connect an external
USB device only to the USB-A host connector.

Fixed peripheral pins belong to their Arduino bus and are not dynamically
assignable. Caller-selected GPIO and SPI chip-select labels are limited to
`D2`–`D7` and `D22`–`D85`. The firmware protects SWD, BOOT0, USB control, radio,
memory, LEDs, onboard crypto, and other internal/fixed pins. A pin claimed by
SPI cannot also be configured as GPIO; conflicting requests fail without
changing the existing owner. Status reports the safe-pin set and active
resource conflicts.

- SPI exposes buses 0 and 1 as master/controller. Wire the selected bus's
  documented SCK/MOSI/MISO pins and one safe caller-owned chip-select.
- I²C exposes buses 0 and 1 as 7-bit master/controller at 100 or 400 kHz.
  `Wire1`, used by the onboard ATECC608A crypto device, remains reserved. The
  v1 Wire transaction bound is 32 bytes in each direction.
- UART exposes hardware ports 0–3 (Arduino `Serial1`–`Serial4`). Cross TX to RX
  and share ground. Do not connect incompatible voltage levels.
- CAN exposes both classic CAN controllers. Each side requires a suitable
  3.3-V logic CAN transceiver; connect CANH/CANL and ground, and terminate the
  two physical ends with 120-ohm resistors. The bridge supports 11/29-bit IDs,
  0–8 data bytes, and 125/250/500/1000 kbit/s. It does not support CAN FD.
- GPIO loopback tests require an explicit output-to-input jumper and common
  ground. Use a series resistor when appropriate for the fixture.

Consult the official GIGA pinout for the fixed bus headers before energizing a
fixture. Compilation proves software support, not the presence or correctness
of physical wiring.

## USB host and wireless boundaries

USB selection is fail-closed: specify exact VID/PID and optionally serial and
interface. Enumeration reports descriptors and control, bulk, and interrupt
endpoints. Hubs and isochronous endpoints are rejected. The bridge is a USB
host only; it does not emulate external USB device classes.

Wi-Fi v1 is station-only and exposes client TCP/UDP sockets. It does not offer
AP/server mode or raw 802.11 injection. BLE v1 is central-only and supports
scan, address selection, discovery, read/write, subscription, and queued
notifications. Pairing is volatile and limited to Just Works or named numeric
confirmation/passkey profiles. Wi-Fi and BLE sessions are mutually exclusive.

Store wireless secrets in a JSON file with mode `0600`, then set
`JLINK_MCP_BRIDGE_PROFILES_FILE` to its container-visible path:

```json
{
  "wifi": {
    "lab": {"ssid": "fixture-net", "password": "replace-me"}
  },
  "ble_passkeys": {
    "sensor": {"passkey": "123456"}
  }
}
```

Only the profile name is public. The service resolves the secret at request
time, transmits it to firmware, and does not place it in commands, results,
errors, audits, or artifacts. Firmware clears assembled request storage after
dispatch and does not persist credentials or bonds across reset.

## Payloads, framing, and receive queues

At the MCP boundary, `data_base64` is canonical padded RFC 4648 base64. For
example, opaque bytes `00 01 fe ff` are `AAH+/w==`. Results include canonical
base64, byte count, SHA-256, at most 64 bytes of hex preview, metadata,
timestamp, overflow state, and the audited command result.

The USB-CDC wire protocol uses COBS-delimited frames, a fixed 20-byte
little-endian header, CRC-32, typed TLVs, request IDs, and ordered segments.
Decoded frames are at most 4096 bytes, assembled messages at most 65536 bytes,
and application payloads at most 64000 bytes. Unknown fields, duplicate TLVs,
bad CRCs, missing/out-of-order/stale segments, unsupported versions, and
unsupported operations fail closed. Firmware produces no unsolicited CDC
text or binary data.

UART, CAN, USB, Wi-Fi, BLE, and GPIO events enter a single 64-KiB bounded queue
store with per-protocol depth and overflow counters. `protocol_bridge_receive`
polls or drains records. Each returned record begins with packed little-endian
`channel:u8`, `payload_length:u16`, and `timestamp_us:u64`, followed by opaque
payload bytes. A full slice drops the new record and increments its protocol's
overflow counter. A non-draining poll leaves records queued.

All bridge operations and radio transitions are serialized. V1 provides no
hard-real-time guarantee and no simultaneous multi-bus latency guarantee.

## Complete SPI example

After deployment, this request clocks four bytes on SPI bus 0, using `D22` as
chip-select, mode 0, MSB first, at 1 MHz:

```json
{
  "request": {
    "operation": "spi_exchange",
    "bus": 0,
    "chip_select": "D22",
    "clock_hz": 1000000,
    "mode": 0,
    "bit_order": "msb_first",
    "fill_byte": 255,
    "data_base64": "AJr//w==",
    "read_length": 4
  },
  "selector": {
    "probe_serial": "<stable-jlink-serial>",
    "board_serial": "<stable-giga-serial>",
    "core": "m7"
  }
}
```

A successful `protocol_bridge_exchange` response has this shape (data depends
on the wired peripheral):

```json
{
  "protocol": "spi",
  "operation": "spi_exchange",
  "data_base64": "EjRWeA==",
  "byte_count": 4,
  "sha256": "<64 lowercase hex characters>",
  "hex_preview": "12345678",
  "metadata": {"bus": 0, "chip_select": "D22", "clock_hz": 1000000, "mode": 0},
  "overflow": false,
  "command": {"backend": "giga-protocol-bridge", "...": "audited fields"}
}
```

Physical HIL is opt-in. Each protocol case reports `available`, `unavailable`,
or `failed` with the exact missing fixture/wiring reason; a compiled feature is
never reported as physically proven.
