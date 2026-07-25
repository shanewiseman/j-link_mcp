# Architecture

## Runtime topology

The host supplies four things to the container: the repository/workspace, a
persistent state directory, USB device access, and a licensed SEGGER 9.62
installation. The image contains only source-available repository code and
pinned open-source Arduino/toolchain dependencies.

```text
LLM/MCP client
    |  loopback HTTP + bearer token (or stdio proxy)
    v
FastMCP tool/resource layer
    v
JLinkService: target identity gate + exclusive probe lease + audit
    +-- Commander backend -------- reset/memory/register/flash/SWO/raw
    +-- GDB backend -------------- managed server, MI, RTT/SWO/telnet ports
    +-- Application backend ------ finite allowlisted SEGGER CLI programs
    +-- Serial backend ----------- stable-board CDC command/telemetry
    +-- GUI backend -------------- Xvfb, AT-SPI, xdotool, OCR, OpenCV
    +-- SDK contract ------------- unavailable until licensed SDK is mounted
    v
J-Link probe -- SWD/SWO --> embedded target
```

## Three tool layers

Atomic MCP tools perform one bounded operation: discovery, connect, target
state, registers, memory, breakpoint/watchpoint, erase/program/verify, GDB/MI,
RTT/SWO, serial, application execution, and GUI inspection/control.

Raw escape hatches accept Commander commands, J-Link command strings,
allowlisted GDB/monitor commands, and allowlisted SEGGER application
arguments. They never invoke a shell. Validators reject shell controls,
GDB host-code facilities, environment mutation, excessive input, and paths
outside `/workspace` or `/state`.

Composed workflows implement preflight, build, dual-core deployment,
boot/observe, breakpoint/watchpoint assertions, controlled-crash capture,
comparison, backup/restore, RTT capture, and evidence report generation.

## Stable identity and concurrency

USB bus/address and `ttyACM` names are transient and are never target keys.
Discovery correlates the J-Link serial, Arduino USB serial, VID/PID, and
topology. Automatic selection succeeds only for one probe and one compatible
board. A previously verified pairing can recover across USB renumbering from
the hash-chained audit history.

Every Commander-changing action first reads live VTref, SW-DP DPIDR, core, and
CPUID through the selected probe. Expected IDs are `0x411FC271` for M7,
`0x410FC241` for M4, and `0x6BA02477` for the SW-DP. A mismatch fails closed.

One exclusive lease exists per probe. Long-lived GDB and GUI sessions hold the
lease until stopped. Separate probes may operate concurrently. Timeouts and
cancellation terminate entire subprocess groups; persisted stale sessions are
cleared and audited at service startup.

## Structured evidence

`CommandResult` includes operation/session IDs, backend, exact argv or
debugger command, timestamps, duration, return code, timeout state, raw output,
parsed values, probe/target identity, before/after state, artifact hashes,
warnings, and evidence paths. Its computed `ok` field is part of the MCP
schema. SQLite stores append-only, SHA-256-linked operations plus sessions and
artifact metadata. Reports verify the chain before emitting JSON and Markdown.

## Firmware fixture

The M7 and M4 sketches use the Arduino GIGA core's build properties and ELF
segments to derive layout. The post-link workflow patches a versioned embedded
manifest with image size, IEEE CRC-32, flash/RAM layout, build identity, and
stable symbol addresses, then regenerates BIN/HEX, symbols, disassembly,
checksums, and the build manifest.

The serial/RTT protocol exposes heartbeat, manifest, information, self-test,
M4 RPC, RAM buffer, breakpoint/watchpoint, controlled HardFault, assertion,
watchdog, deadlock, reset, stack, step, and SWO facilities. Normal fixture code
does not write option bytes or the Arduino bootloader.
