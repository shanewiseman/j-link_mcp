# GIGA protocol bridge

`giga_protocol_bridge` is an optional trusted MCP extension depending on
`arduino_giga`. It owns a versioned M7 firmware image and bounded MCP requests
for SPI, I2C, UART, classic CAN, USB host, Wi-Fi, BLE central, and protected
GPIO. USB-C CDC is the control plane; external USB devices use the host port.

Enable both IDs and use the maintained GIGA overlay. `deploy_protocol_bridge`
backs up full flash, verifies the checked release, programs it through the
positively identified M7 access port, and requires a version handshake. Use
`get_protocol_bridge_status`, `protocol_bridge_control`,
`protocol_bridge_exchange`, and `protocol_bridge_receive` for bounded actions.
`build_protocol_bridge_release` rebuilds/compares the deterministic release.

Receive queues are partitioned by protocol and selected by the request's
`channel` (UART port, CAN bus, USB endpoint, Wi-Fi socket, BLE channel, or GPIO
pin). A receive returns only complete records for that channel. With
`drain=true`, only matching records actually returned within `limit` are
removed; unmatched channels and the first record that does not fit remain in
FIFO order. With `drain=false`, all records remain queued. Timeout and reported
remaining depth are evaluated for the selected channel.

Payloads cross MCP as canonical base64 and remain opaque. Resource ownership
prevents conflicting pin/peripheral use. Safe GPIO pins are enumerated by the
extension; voltage levels, current limits, external CAN transceivers,
termination, USB power, and wiring remain operator responsibilities.

The bridge bus numbers map to the pinned Arduino GIGA platform as follows:

| Bridge interface | GIGA pins |
|---|---|
| SPI bus 0 (`SPI`) | D90/MOSI, D89/MISO, D91/SCK; caller-owned safe CS |
| SPI bus 1 (`SPI1`) | D11/MOSI, D12/MISO, D13/SCK; D10 is conventional CS |
| I2C bus 0 (`Wire`) | D20/SDA, D21/SCL |
| I2C bus 1 (`Wire2`) | D9/SDA, D8/SCL |

`Wire1` is reserved for the onboard ATECC608A. D8/D9 may instead be claimed as
GPIO, and D10 may be claimed as GPIO or an SPI chip select. These overlapping
uses are mutually exclusive: resource ownership rejects I2C bus 1 or CAN1 when
D8/D9 are GPIO-owned and rejects a conflicting use of D10 after SPI claims it.

Wi-Fi and BLE are mutually exclusive. Secret profiles must be mode `0600` and
are referenced through extension configuration; secrets are never returned.

The protocol HIL suite requires `JLINK_MCP_PROTOCOL_HIL=1` and a JSON fixture
description in `JLINK_MCP_PROTOCOL_HIL_FIXTURES`. It reports every missing
physical protocol companion as unavailable and always restores the original
full-flash backup. Follow both nested agent contracts before running it.
