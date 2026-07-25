# Protocol bridge firmware

This M7-only sketch implements wire version 1 of the GIGA universal protocol
bridge. `protocol_bridge.ino` owns the transport implementations,
`BridgeWire.h` defines the COBS/CRC/TLV contract shared with the Python codec,
and `BridgeResources.h` defines safe dynamic pin ownership and the bounded
receive queues.

The release workflow stages this directory, injects
`BridgeBuildIdentity.generated.h`, compiles the `cm7,split=75_25` image with the
pinned container toolchain, and compares the produced HEX to `release/`
byte-for-byte. Do not add generated ELF, BIN, map, or debug files here; they
belong in managed runtime state.

See `docs/protocol-bridge.md` for wiring, supported roles, secrets, receive
record layout, deployment, and recovery.
