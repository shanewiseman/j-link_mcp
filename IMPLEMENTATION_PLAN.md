# J-Link MCP Implementation and Hardware-in-the-Loop Plan

## Handoff directive

Implement this plan entirely within this repository. Treat the requirements and
decisions below as authoritative. Before changing code, inspect the repository,
the host's installed SEGGER package, attached hardware, and current dependency
versions. Preserve unrelated user changes.

The implementation is not complete until the Dockerized MCP has built, flashed,
tested, debugged, and verified the custom validation firmware on the Arduino
GIGA R1 through MCP calls, and has produced the evidence described in the
acceptance criteria.

## Summary

Implement the complete project in `/home/swiseman/repositories/j-link_mcp`. The
repository will produce:

- A Dockerized Python MCP server with atomic J-Link tools, composed workflows,
  raw command access, and SEGGER GUI automation.
- A custom Arduino GIGA R1 validation firmware for both STM32H747 M7 and M4
  cores.
- An end-to-end hardware-in-the-loop suite that builds, flashes, controls,
  tests, and verifies that firmware exclusively through the MCP interface.

Initial integration uses SEGGER CLI, GDB, RTT, SWO, and GUI tools. A stable
adapter contract allows the separately licensed J-Link SDK to be added later.

## Repository and dependencies

- Keep all source, Docker configuration, documentation, firmware fixtures,
  schemas, and tests inside the `j-link_mcp` repository.
- Organize the implementation around:
  - The MCP server and backend adapters in the repository's Python package.
  - Custom firmware under `firmware/giga_hil`.
  - End-to-end MCP tests under `tests/hil`.
- Publish only source-available code and build definitions. Mount the user-installed
  SEGGER 9.62 package read-only; never commit or distribute SEGGER binaries,
  firmware, SDK headers, or licensed products.
- Use Python 3.12 and pin `mcp==1.27.2` with a `<2` constraint until a controlled
  v2 migration.
- Include Arduino CLI 1.5.1, `arduino:mbed_giga@4.6.0`, Arm GCC/GDB, OpenOCD,
  DFU, imgtool, SVD files, bootloader assets, ELF analysis, serial, USB
  discovery, and GUI automation dependencies in the project's Docker image.
- Run SEGGER GUI applications in isolated Xvfb with accessibility automation,
  `xdotool`, OCR, image matching, and MCP-returned screenshots.
- Support Linux x86-64 first. Pass through USB using restricted cgroup rules for
  USB, ACM, and USB-serial devices without `--privileged`. Use group-based
  `0660` udev rules.
- Bind Streamable HTTP only to `127.0.0.1`, require a generated bearer token,
  and provide a stdio-to-HTTP MCP shim.
- Respect the EDU Mini's non-profit educational-use restriction and hardware
  limits. Commercial users must supply appropriately licensed probes and
  software.

### Host prerequisites

- Linux x86-64 initially; the current Ubuntu host qualifies.
- Docker Engine and Docker Compose with cgroup v2 device rules.
- A one-time group-based udev setup for SEGGER `1366:*`, Arduino `2341:*`, ACM
  serial, and USB serial devices using mode `0660`.
- User membership in `docker`, `plugdev`, and `dialout`.
- Full SWD wiring: VTref, GND, SWDIO, SWCLK, nRESET, and optional SWO.
- Separate USB power for the GIGA. The EDU Mini cannot supply target power and
  has a maximum SWO sampling frequency of 4 MHz.

### Container device access

- Bind `/dev/bus/usb` at its standard path for SEGGER and libusb access.
- Bind host `/dev` read-only under `/host/dev` for hot-plug-aware serial
  discovery.
- Permit only required character-device classes through cgroups:
  - USB: `189:*`
  - ACM: `166:*`
  - USB serial: `188:*`
- Mount USB sysfs read-only for VID/PID, serial, topology, and hot-plug
  correlation.
- Do not use `--privileged`; drop unrelated Linux capabilities.
- Run the service as a non-root user.

### Proprietary SEGGER boundary

- Validate against J-Link Software and Documentation Pack 9.62.
- Mount the user's architecture-matched SEGGER installation read-only at
  runtime.
- Keep SEGGER settings, logs, GUI projects, and generated command files in a
  separate writable volume.
- Never publish SEGGER binaries, firmware images, SDK headers, manuals, or
  licensed GUI products in the image or repository.
- Detect executable presence, version, probe licenses, and model limitations at
  startup.
- Support optional read-only mounts for separately installed Ozone and
  SystemView packages. Missing or unlicensed products must appear as unavailable
  capabilities.
- The attached EDU Mini may only be used for qualifying non-profit educational
  work. Public source availability does not grant commercial use of the probe or
  SEGGER software.
- Direct API integration remains disabled until a valid J-Link SDK package and
  license are supplied.

### Open container stack

- Python 3.12.
- `mcp==1.27.2`, explicitly constrained below v2.
- Lock all direct and transitive Python dependencies in `uv.lock`, including:
  - `pyudev`
  - `pyserial`
  - `pyelftools`
  - `pygdbmi`
  - HTTP/token middleware
  - Image and GUI automation dependencies
  - Test dependencies
- Arduino CLI 1.5.1 and `arduino:mbed_giga@4.6.0`.
- Pin the GIGA-bundled Arm GCC/GDB, OpenOCD, DFU, imgtool, SVD files, and
  bootloader assets for reproducible builds.
- Accept external ELF, HEX, and BIN artifacts regardless of the toolchain that
  produced them.
- Handle `STM32H747XI_M7` and the corresponding M4 target. Derive memory layouts
  from Arduino build metadata instead of hard-coding addresses.

### GUI automation tier

- Use an isolated Xvfb display rather than the host X server.
- Install a lightweight window manager, AT-SPI accessibility tools, `xdotool`,
  screenshot utilities, Tesseract OCR, and OpenCV-based image matching.
- Prefer accessibility selectors and semantic state. Use version-pinned image
  recognition only as a fallback.
- Return screenshots and automation traces through MCP.
- Provide an optional loopback-only noVNC diagnostic profile.
- Treat GUI automation as a fallback when no supported headless interface can
  perform the operation.

## MCP capability contract

### Discovery and capability reporting

- Discover and correlate:
  - J-Link serial number, model, firmware, licenses, and USB identity.
  - Arduino USB serial, VID/PID, and topology.
  - Target voltage, MCU ID, core identity, and configured board profile.
- Automatically choose a probe/target pairing only when exactly one valid match
  exists. Fail closed on ambiguity.
- Reconnect by stable serial number, never by transient USB bus address.
- Publish a structured capability manifest containing:
  - Probe identity and hardware limits.
  - Installed SEGGER applications and versions.
  - Available licenses.
  - Target and core.
  - Supported interfaces and speeds.
  - Available commands, workflows, and GUI backends.
  - Trace, RTT, SWO, and serial support.
  - Explicit reasons for every unavailable capability.

### Tool layers

Provide three complementary layers:

1. Atomic tools:
   - Probe and target discovery.
   - Connect and disconnect.
   - Reset, halt, run, and step.
   - Register and memory reads/writes.
   - Breakpoints and watchpoints.
   - Flash, erase, load, compare, and verify.
   - GDB server and GDB/MI control.
   - RTT, SWO, ITM, semihosting, and serial capture.
   - Trace controls where hardware supports them.
   - Probe configuration, firmware information, power, and target-interface
     configuration.
   - SEGGER GUI startup, inspection, automation, and screenshot capture.

2. Raw escape hatches:
   - J-Link Commander commands.
   - J-Link command strings.
   - GDB and GDB monitor commands.
   - Supported SEGGER application arguments and command files.
   - Raw access must not expose a host shell.
   - Reject shell metacharacters, environment expansion, and paths outside
     configured roots.

3. Composed workflows:
   - Dependency and hardware preflight.
   - Firmware build.
   - Flash and verify.
   - Boot and observe.
   - Breakpoint and watchpoint assertions.
   - Crash capture and analysis.
   - Dual-core deployment.
   - Regression execution.
   - Firmware comparison.
   - Backup and restoration.
   - Diagnostic and validation report generation.

### Structured results and sessions

- Standardize every result with:
  - Probe and target identities.
  - Session and operation IDs.
  - Selected backend and exact commands.
  - Parsed structured data.
  - Raw stdout and stderr.
  - Start time, duration, and timeout status.
  - Input and output artifact hashes.
  - Target state before and after the operation.
  - Warnings, screenshots, and evidence attachments.
- Maintain one exclusive actor or lease per probe.
- Serialize conflicting operations.
- Permit concurrency only across separate probes.
- Use deterministic timeouts, cancellation, process-group cleanup, and stale
  session recovery.
- Store sessions, audit history, transcripts, screenshots, and artifact metadata
  in a persistent SQLite/state volume.

### Security and autonomy

- Run a persistent Streamable HTTP MCP server bound to `127.0.0.1`.
- Require a generated bearer token stored outside the image.
- Provide a stdio shim for clients that cannot connect over HTTP.
- Mount explicitly configured firmware roots read/write at `/workspace`.
- Reject artifact and command-file paths outside allowed roots.
- Expose all J-Link operations without per-command confirmation, including
  destructive operations, as required by this plan.
- Require positive target identification before any operation.
- Record immutable audit entries for every action.
- Mark destructive MCP tools accurately so the client can apply its configured
  policy.
- Do not require runtime internet access after the image and toolchains have
  been assembled.

## Custom GIGA validation firmware

Build dedicated M7 and M4 validation images with the pinned Arduino GIGA core.
Derive flash regions and M7/M4 partitioning from Arduino build properties rather
than hard-coding addresses.

### Embedded manifest

Each firmware image must contain a machine-readable manifest with:

- A unique magic identifier.
- Firmware and test-protocol versions.
- Repository Git commit and build ID.
- M7 or M4 core identity.
- Flash and RAM layout.
- Build timestamp.
- Image size and CRC.
- Addresses of designated symbols and test buffers derived from the ELF.

### Deterministic test facilities

Implement:

- Heartbeat and uptime counters.
- M7/M4 boot and shared-memory handshake.
- Predictable global variables for memory, breakpoint, and watchpoint tests.
- Stable named functions for halt, step, breakpoint, stack, and backtrace tests.
- RTT and USB serial command/response channels.
- ITM/SWO event generation when SWO is available.
- Controlled logging and test-pattern buffers.
- Explicit HardFault, assertion, watchdog, and deadlock triggers.
- Firmware self-tests covering RAM, flash-readable constants, inter-core
  communication, timing, and peripheral-independent behavior.
- A deterministic protocol for querying state and triggering each fixture.

Normal validation firmware must not modify option bytes or overwrite the Arduino
bootloader.

Store the generated ELF, HEX or BIN, map file, symbols, disassembly, build
manifest, and checksums as MCP-managed artifacts.

## MCP-only hardware validation

The end-to-end hardware suite must act as an MCP client. It must not invoke
J-Link Commander, GDB Server, Arduino CLI, Docker, or serial utilities directly,
except for initial container startup and explicit failure-recovery diagnostics.

Run this sequence:

1. Call the MCP dependency/preflight workflow.
2. Uniquely identify the EDU Mini and GIGA.
3. Read target identity, VTref, M7/M4 IDs, option bytes, and current flash
   layout.
4. Back up application flash and record hashes when readout permits.
5. Build the custom M7 and M4 firmware through the MCP build workflow.
6. Flash through MCP and verify programmed bytes and CRC.
7. Prove the bootloader and option bytes were preserved.
8. Reset and run both cores.
9. Validate manifests, heartbeat, inter-core handshake, serial output, and RTT.
10. Set symbolic breakpoints and watchpoints, continue execution, and confirm
    expected hits.
11. Inspect registers, stack, locals, symbols, and memory.
12. Step instructions and source lines.
13. Modify and verify only the designated RAM test buffer.
14. Capture SWO and ITM output where wiring and probe capability permit.
15. Trigger controlled faults and verify halt reason, register capture,
    backtrace, stack evidence, memory evidence, and recovery.
16. Exercise disconnect/reconnect, USB renumbering, stale sessions, timeouts,
    cancellation, and subprocess cleanup.
17. Restore the original application image when it was backed up.
18. Verify the restored hash and boot behavior.
19. Generate a machine-readable and human-readable validation report.

If the original firmware cannot be backed up, require a one-time
`test_target_disposable=true` configuration before destructive hardware
validation. Do not require per-test confirmation after that setting is present.

Test irreversible protection, provisioning, option-byte, and mass-erase
operations through mocks or a separately designated sacrificial target, not the
primary GIGA.

Run GUI validation through MCP tools that launch applications in Xvfb, automate
representative operations, and return screenshots and structured evidence.

## Verification strategy

### Dependency doctor

Verify:

- Docker, Compose, and cgroup support.
- Required group membership and udev permissions.
- Device mounts and access without `--privileged`.
- MCP bearer-token configuration.
- SEGGER path, version, executables, and detected licenses.
- Arduino CLI, board core, compiler, debugger, uploader, and analysis tools.
- GUI runtime libraries and virtual display.
- Workspace and state-volume permissions.
- Attached J-Link and GIGA identities.
- Read-only connection to M7 and M4.
- VTref, MCU IDs, memory map, and debug access without changing flash or option
  bytes.

### Automated tests

- Unit tests for schemas, parsers, capability rules, target profiles, command
  validation, leases, and workflow state machines.
- Golden-output tests for all supported SEGGER and GDB versions.
- Fake SEGGER, GDB, serial, RTT, SWO, and GUI backends.
- Failure coverage for malformed output, hangs, disconnects, timeouts, process
  crashes, stale locks, and target-state changes.
- Container tests proving:
  - Non-root execution.
  - Restricted device access.
  - Token enforcement.
  - Path confinement.
  - No host-shell escape through raw tools.
  - No proprietary files in the published image.
- Hot-plug, USB renumbering, multiple-probe ambiguity, cancellation, and process
  cleanup tests.
- GUI tests that launch each installed application, navigate representative
  workflows, capture screenshots, and verify expected UI state.

### GIGA hardware acceptance

- Build and flash the repository's custom M7 and M4 fixtures.
- Verify image bytes, CRC, manifest, symbols, and build identity.
- Preserve the bootloader and option bytes.
- Validate both cores, shared memory, serial, RTT, and optional SWO.
- Exercise registers, memory, breakpoints, watchpoints, stepping, fault capture,
  reset, and recovery through MCP.
- Validate externally produced ELF, HEX, and BIN inputs.
- Produce complete evidence and restore the prior firmware when possible.

## Acceptance criteria

- A clean checkout builds the source-available container without proprietary SEGGER
  files.
- A dependency doctor reports every satisfied or missing host, container,
  hardware, license, and wiring requirement.
- After one-time Docker, udev, token, workspace, and MCP-client authorization,
  no per-operation operating-system permission requests are required.
- The custom GIGA firmware is built, flashed, verified, executed, debugged,
  fault-tested, and optionally restored entirely through MCP calls.
- Every installed and licensed J-Link operation is reachable through a typed
  tool, workflow, raw escape hatch, or automated GUI backend.
- Unsupported capabilities return precise structured dependency explanations
  instead of silently degrading.
- Unit, parser, mock-backend, container-security, GUI, and real-hardware suites
  pass.
- Validation produces machine-readable and human-readable reports containing
  logs, hashes, screenshots, commands, target states, and hardware evidence.
- The repository and published image contain no proprietary SEGGER artifacts.
- The repository includes an SBOM and third-party license inventory.

## Fixed decisions and constraints

- Repository: `/home/swiseman/repositories/j-link_mcp`.
- Language: Python unless a demonstrated compatibility requirement forces a
  narrowly scoped native component.
- Runtime: Docker on Linux x86-64.
- MCP SDK: stable 1.27.x line, initially pinned to 1.27.2.
- Transport: token-protected loopback Streamable HTTP plus a stdio shim.
- SEGGER delivery: read-only host mount.
- Toolchain image: Arduino GIGA and generic Arm/ELF tooling in one image.
- Initial target: fully certify Arduino GIGA R1 M7/M4 while retaining generic
  probe and target adapters.
- Probe selection: automatic only for a unique verified match.
- Tool surface: typed tools, composed workflows, and validated raw access.
- Autonomy: expose all supported operations without per-operation confirmation.
- GUI scope: automate installed SEGGER GUI applications in isolated Xvfb.
- SDK scope: define the adapter now and add direct SDK support only after the
  required license and package are supplied.
- Source license: PolyForm Noncommercial 1.0.0. Copying, modification, and
  noncommercial redistribution are permitted; sale and revenue-generating use
  are not licensed. Maintain an SBOM and third-party license inventory.
- Hardware limitation: software must never claim capabilities absent from the
  connected probe, target, wiring, installed products, or licenses.
- GUI limitation: SEGGER upgrades are not accepted until headless, parser, GUI,
  OCR, and screenshot regression tests pass.

## Authoritative references

- [SEGGER J-Link EDU Mini](https://www.segger.com/products/debug-probes/j-link/models/j-link-edu-mini/)
- [SEGGER J-Link SDK](https://www.segger.com/products/debug-probes/j-link/tools/j-link-sdk/)
- [SEGGER J-Link Commander](https://kb.segger.com/J-Link_Commander)
- [SEGGER J-Link command strings](https://kb.segger.com/J-Link_command_strings)
- [SEGGER STM32H7 notes](https://kb.segger.com/STM32H7)
- [Arduino GIGA R1 WiFi](https://docs.arduino.cc/hardware/giga-r1-wifi)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Docker Compose device mappings](https://docs.docker.com/reference/compose-file/services/)
