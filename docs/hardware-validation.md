# Hardware validation

Core supplies target-neutral primitives and does not ship a board fixture or
destructive HIL sequence. Hardware acceptance belongs to the extension that
defines the target profile, firmware, identity values, backup range, and safe
restoration procedure.

The maintained first-party procedure is the
[Arduino GIGA hardware-validation guide](../extensions/arduino_giga/docs/hardware-validation.md).
Protocol bridge fixture validation is documented by the
[bridge extension](../extensions/giga_protocol_bridge/docs/protocol-bridge.md).

For any target: run doctor/capabilities first, use stable selectors, preserve a
full authorized backup before destructive work, perform target changes only
through MCP after startup, restore and independently verify original bytes,
reset to run, reconnect, and verify the audit chain.
