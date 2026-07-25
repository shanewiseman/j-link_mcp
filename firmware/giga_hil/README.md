# Arduino GIGA hardware-in-the-loop fixture

The `m7` and `m4` sketches form one validation fixture for the STM32H747. They
expose stable `jlink_mcp_*` symbols, a discoverable firmware manifest, RTT and
serial telemetry, dual-core RPC state, and controlled fault sites.

Build and flash these sketches through the MCP tools. Direct `arduino-cli` and
J-Link invocations are permitted only while developing or recovering the MCP
itself; acceptance tests use the MCP client exclusively.

The default certified partition is `75_25`: 1.5 MiB for M7 and 0.5 MiB for M4.
The build workflow still obtains addresses and limits from Arduino build
properties and ELF segments.
