# Arduino GIGA extension

`jlink-mcp-arduino-giga` is a trusted, explicitly enabled first-party extension
for Arduino GIGA R1 discovery, positive M7/M4 identity, firmware builds,
dual-core workflows, deterministic fixtures, and hardware validation.

Enable it with `JLINK_MCP_EXTENSIONS=arduino_giga`. The maintained combined
container also enables the dependent `giga_protocol_bridge` extension through
`compose.giga.yaml`.

See [operations](docs/operations.md) for installation/container setup and the
[hardware-validation contract](docs/hardware-validation.md) before changing an
attached target. Configuration, firmware, tests, SBOM, licensing, and agent
instructions are owned by this package.
