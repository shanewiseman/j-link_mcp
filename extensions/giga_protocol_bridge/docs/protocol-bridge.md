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

Payloads cross MCP as canonical base64 and remain opaque. Resource ownership
prevents conflicting pin/peripheral use. Safe GPIO pins are enumerated by the
extension; voltage levels, current limits, external CAN transceivers,
termination, USB power, and wiring remain operator responsibilities.

Wi-Fi and BLE are mutually exclusive. Secret profiles must be mode `0600` and
are referenced through extension configuration; secrets are never returned.

The protocol HIL suite requires `JLINK_MCP_PROTOCOL_HIL=1` and a JSON fixture
description in `JLINK_MCP_PROTOCOL_HIL_FIXTURES`. It reports every missing
physical protocol companion as unavailable and always restores the original
full-flash backup. Follow both nested agent contracts before running it.
