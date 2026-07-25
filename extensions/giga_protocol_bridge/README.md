# GIGA protocol bridge extension

`jlink-mcp-giga-protocol-bridge` packages the optional universal bridge
firmware, versioned wire codec, typed requests, serial backend, deterministic
release workflow, deployment flow, and MCP tools. It depends on the separately
enabled `arduino_giga` extension.

Enable both IDs with
`JLINK_MCP_EXTENSIONS=arduino_giga,giga_protocol_bridge` or use the maintained
`compose.giga.yaml` overlay.

Read the [canonical bridge guide](docs/protocol-bridge.md) before deployment or
physical fixture work. The checked firmware release, pinned-library inventory,
tests, licensing, and hardware-specific agent contract are owned here.
