# J-Link MCP

Give an AI engineering agent a safe, observable connection to real embedded
hardware.

J-Link MCP turns a SEGGER J-Link probe into a capability-aware Model Context
Protocol server. An MCP client can discover the attached probe and target,
build firmware, program and verify flash, debug both cores, inspect live state,
exercise serial/RTT/SWO channels, automate installed SEGGER applications, and
produce tamper-evident validation evidence—all through typed tools rather than
an unrestricted host shell.

The certified reference target is the Arduino GIGA R1 WiFi and its STM32H747
M7/M4 cores. The adapter and capability model remain generic enough to add
other probes and target profiles without weakening target selection or safety
checks.

## What you get

- **Hands-free firmware workflows.** Build, flash, verify, boot, observe,
  debug, fault-test, compare, back up, and restore through MCP calls.
- **Hardware truth before every change.** Stable probe and board serials,
  VTref, SW-DP identity, core CPUID, target profile, firmware, and licenses are
  checked before target-changing operations.
- **Three levels of control.** Small typed tools, multi-step workflows, and
  validated Commander/GDB/application escape hatches cover both common and
  advanced J-Link use.
- **Dual-core GIGA support.** Build and deploy independent M7/M4 images,
  transiently release a boot-held M4, validate shared behavior, and debug each
  core with its correct target identity.
- **Managed debug and observability.** Exclusive GDB sessions, GDB/MI,
  breakpoints, watchpoints, registers, memory, stack/backtrace capture, RTT,
  semihosting, USB serial, and optional SWO/ITM.
- **SEGGER application access without redistribution.** The locally licensed
  J-Link Software Pack is mounted read-only. Headless tools and installed GUI
  applications are exposed through allowlisted, audited backends.
- **Evidence you can hand to another engineer.** Every backend result includes
  exact commands, raw and parsed output, timing, identities, target states,
  hashes, warnings, and evidence paths. SQLite records are SHA-256 chained;
  validation reports are emitted as JSON and Markdown.
- **A hardened local runtime.** The service is non-root, capability-free,
  read-only except for explicit workspace/state mounts, restricted to required
  device classes, bound to loopback, and protected by a generated bearer token.

## Product architecture

```text
MCP client / AI agent
        |
        | Streamable HTTP on 127.0.0.1 + bearer token
        | or authenticated stdio bridge
        v
J-Link MCP
  capability discovery + stable identity gate + exclusive probe lease
        |
        +-- Typed atomic tools
        +-- Composed firmware/debug/validation workflows
        +-- Validated Commander, GDB, and application surfaces
        +-- Xvfb GUI automation with AT-SPI, screenshots, OCR, image matching
        |
        v
Read-only SEGGER installation --> J-Link probe --> SWD/SWO target
                                      |
                                      +--> target USB serial / RTT
```

The container includes Python, Arduino CLI, the pinned Arduino GIGA platform,
GNU Arm tools, OpenOCD, DFU tooling, ELF analysis, and GUI automation support.
It does **not** include SEGGER binaries, firmware, SDK files, or manuals.

## Certified baseline

| Area | Certified configuration |
|---|---|
| Host | Linux x86-64 with cgroup v2 and Docker Compose |
| Runtime | Python 3.12, MCP Python SDK 1.27.2 |
| SEGGER package | J-Link Software and Documentation Pack 9.62, mounted read-only |
| Probe | J-Link EDU Mini V2, selected by serial number |
| Target | Arduino GIGA R1 WiFi, STM32H747XI M7 and M4 |
| Target interface | SWD; optional SWO wire |
| Arduino platform | Arduino CLI 1.5.1 and `arduino:mbed_giga@4.6.0` |
| Firmware inputs | ELF, Intel HEX, and explicit-address BIN |
| MCP transport | Token-protected loopback Streamable HTTP; stdio bridge available |

Other connected devices and installed products are reported dynamically. A
capability is never silently assumed: unavailable tools and workflows include
their missing dependency or hardware reason. See the full
[support matrix](docs/support-matrix.md).

## Before you start

You need:

- Linux x86-64 with Docker Engine and Docker Compose v2.
- A locally installed and appropriately licensed SEGGER J-Link Software Pack
  9.62 for x86-64.
- Membership in `docker`, `plugdev`, and `dialout`.
- A J-Link connected to VTref, GND, SWDIO, SWCLK, and nRESET.
- A separately USB-powered Arduino GIGA. The EDU Mini cannot power the target.
- Optional SWO wiring if SWO/ITM capture is required.

The EDU Mini is restricted by SEGGER to qualifying non-profit educational
use. Commercial use requires an appropriately licensed probe and software.

## Quick start

### 1. Prepare device permissions

Install the repository's restrictive udev policy:

```sh
scripts/install-udev-rules.sh
```

The installer intentionally loads `59-jlink-mcp.rules` before Arduino's
world-writable vendor rule and locks J-Link/Arduino USB nodes to `0660` with
`plugdev` or `dialout` ownership. Reconnect the probe and board if their modes
do not update immediately. The installer checks every matching live device and
fails if any node is not exactly `0660` with its expected group.

If group membership was just added, log out and back in before continuing.

### 2. Create local configuration and credentials

If SEGGER is installed somewhere other than `/opt/SEGGER/JLink_V962`, export
its path first:

```sh
export SEGGER_ROOT=/path/to/JLink_V962
scripts/bootstrap.sh
```

Bootstrap creates:

- `.env.hardware` with the host UID/GID, device groups, current GIGA serial
  node, SEGGER path, and disposable-target policy.
- `.token`, a mode-`0600` bearer token.
- `state/`, the ignored persistent evidence and SEGGER-settings directory.

Keep `.token`, `.env.hardware`, and `state/` private. They are excluded from
Git and from the Docker build context.

### 3. Start the product

With standard Docker Engine:

```sh
docker compose --env-file .env.hardware up --build -d
docker compose --env-file .env.hardware ps
curl --fail http://127.0.0.1:8000/healthz
```

Canonical's snap-packaged Docker currently requires the included compatibility
overlay:

```sh
docker compose --env-file .env.hardware \
  -f compose.yaml -f compose.snap.yaml up --build -d
```

The service is ready when Compose reports `healthy` and the health endpoint
returns `{"status":"ok"}`.

### 4. Connect an MCP client

Use this Streamable HTTP endpoint:

```text
http://127.0.0.1:8000/mcp
```

Send the token from `.token` in every MCP request:

```text
Authorization: Bearer <token>
```

For clients that only support stdio, install the local Python package and run
the authenticated bridge:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
JLINK_MCP_TOKEN_FILE=.token \
  .venv/bin/jlink-mcp stdio-proxy --url http://127.0.0.1:8000/mcp
```

The bridge carries MCP JSON-RPC over stdio; it does not expose a host shell.

### 5. Start with discovery

An agent should begin each hardware session with:

1. `get_capabilities`
2. `dependency_doctor`
3. `hardware_preflight`

These calls establish what is actually installed and connected, fail closed
on ambiguous probe/board selection, confirm both GIGA cores and VTref, and
return precise remediation for anything unavailable.

## Everyday workflows

| Goal | MCP workflow |
|---|---|
| Build a GIGA image | `build_giga_firmware` |
| Program ELF/HEX and verify it | `flash_and_verify` |
| Program an explicit-address BIN | `flash_binary` |
| Build and deploy both GIGA cores | `deploy_dual_core_firmware` |
| Release and identify a boot-held M4 | `prepare_giga_dual_core_debug` |
| Reset, observe manifests, heartbeats, self-tests, and M4 RPC | `boot_and_observe` |
| Assert breakpoints, watchpoints, registers, memory, stack, and stepping | `assert_debug_fixture` |
| Trigger, capture, analyze, and recover a controlled HardFault | `capture_controlled_crash` |
| Capture RTT using the ELF-derived control block | `capture_rtt` |
| Compare target flash with an artifact | `compare_firmware` |
| Back up or restore flash with hash authorization | `backup_flash`, `restore_flash_backup` |
| Run the repository's complete fixture workflow | `validate_giga_fixture` |
| Export the evidence record | `generate_validation_report` |

Atomic tools are also available for probe discovery, connect/disconnect,
reset/halt/run/step, memory and registers, breakpoints and watchpoints, erase
and verify, USB serial, SWO, managed GDB sessions and channels, ELF inspection,
SEGGER application execution, GUI automation, audit inspection, and audit-chain
verification.

Advanced operations use `raw_commander`, `raw_jlink_command_string`,
`gdb_command`, and `run_segger_application`. These are deliberately not shell
escape hatches: command names, metacharacters, debugger facilities, executable
names, argument counts, and paths are validated and confined to `/workspace`
or `/state`.

## Hardware-safe validation

The repository includes deterministic M7 and M4 firmware under
`firmware/giga_hil`. The fixture exposes embedded build manifests, stable debug
symbols, heartbeat and uptime counters, shared-core behavior, USB serial and
RTT protocols, RAM test buffers, break/watch sites, SWO events, self-tests, and
controlled failure triggers.

The full GIGA acceptance test operates as an MCP client after container startup.
It:

1. Identifies the exact probe, board, M7, M4, and target voltage.
2. Snapshots bootloader, option/protection, boot, and flash evidence.
3. Backs up and hashes all 2 MiB of application flash.
4. Builds, flashes, and byte-verifies both custom images through MCP.
5. Exercises serial, RTT, optional SWO, memory, registers, breakpoints,
   watchpoints, stepping, stack/backtrace, and a controlled HardFault.
6. Validates ELF, HEX, and BIN programming paths.
7. Restores the original full-flash image in `finally` and requires an identical
   post-restore SHA-256.
8. Generates lossless JSON plus human-readable Markdown evidence.

If readable flash cannot be backed up, destructive validation stops unless a
separate sacrificial target was explicitly configured with
`TEST_TARGET_DISPOSABLE=true`. Irreversible provisioning, readout protection,
option-byte programming, and mass erase are never tested on the primary GIGA.

## Evidence and state

Persistent runtime data lives under ignored `state/` subdirectories:

| Path | Contents |
|---|---|
| `state/jlink-mcp.sqlite3` | Hash-chained operations, sessions, and artifact metadata |
| `state/commands/` | Exact generated Commander command files |
| `state/artifacts/` | Firmware builds, backups, comparisons, RTT, and hashes |
| `state/screenshots/` | GUI evidence returned by MCP |
| `state/reports/<run-id>/` | Complete JSON and Markdown validation reports |
| `state/segger/` | Writable SEGGER settings separated from licensed binaries |

The JSON report is the lossless record. The Markdown companion summarizes
hardware, dependencies, commands and target states, recent logs, artifact
hashes, screenshots, and evidence paths for human review.

## Optional product profiles

- `compose.novnc.yaml` exposes a loopback-only noVNC diagnostic view of the
  isolated GUI display.
- `compose.ozone.yaml` mounts a separately installed, licensed Ozone package
  read-only when `OZONE_ROOT` is supplied.
- `compose.systemview.yaml` does the same for SystemView through
  `SYSTEMVIEW_ROOT`.
- `compose.snap.yaml` is only for Canonical's snap Docker runtime; standard
  Docker Engine should use the base Compose file.

Missing optional products remain structured unavailable capabilities. The
J-Link SDK adapter is intentionally disabled until a separately licensed SDK
package is supplied and reviewed.

## Operating the service

```sh
# Status and logs
docker compose --env-file .env.hardware ps
docker compose --env-file .env.hardware logs --tail=100 mcp

# Stop the service without deleting persistent evidence
docker compose --env-file .env.hardware down

# Rebuild after a source change
docker compose --env-file .env.hardware up --build -d
```

Add `-f compose.yaml -f compose.snap.yaml` to those commands on the snap Docker
host.

### Troubleshooting

- **No probe or board:** run `lsusb`, confirm `1366:*` for J-Link and `2341:*`
  for GIGA, then rerun the udev installer and reconnect both devices.
- **Permission warning:** expect USB nodes to be `0660 root:plugdev` and serial
  nodes `0660 root:dialout`; world-writable `0666` is intentionally rejected.
- **Ambiguous hardware:** pass stable `probe_serial` and `board_serial` values;
  the server will not guess when multiple candidates exist.
- **M4 cannot attach after restoring user firmware:** call
  `prepare_giga_dual_core_debug`; it transiently sets the runtime boot request
  and does not alter flash or programmed option bytes.
- **SWO is empty:** SWO support and a physical SWO wire are separate facts.
  Inspect the capability reason and verify the wiring and requested speed.
- **Unauthorized MCP response:** confirm the client is reading the current
  `.token` and sending the bearer header to the loopback endpoint.
- **Snap Docker rejects container startup:** use `compose.snap.yaml`; prefer
  standard Docker Engine for the strongest `no-new-privileges` profile.

More detailed recovery guidance is in [installation and operations](docs/operations.md).

## Development and verification

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
export PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH"

# Compilation, unit/mock coverage, Compose validation, SBOM, and boundary scan
scripts/validate.sh

# Running-container isolation and proprietary-boundary assertions
JLINK_MCP_CONTAINER_TEST=1 .venv/bin/python -m pytest tests/container -v

# Every advertised installed SEGGER GUI through MCP/Xvfb
JLINK_MCP_GUI=1 .venv/bin/python -m pytest tests/hil/test_gui_acceptance.py -v

# Full destructive GIGA HIL with guaranteed restoration
JLINK_MCP_HIL=1 .venv/bin/python -m pytest tests/hil/test_giga_acceptance.py -v
```

Unit and fake-backend tests require no hardware. The GUI and HIL suites require
the running container and explicit opt-in environment flags. See
[hardware validation](docs/hardware-validation.md) before running the HIL test.

Regenerate software-composition evidence with:

```sh
scripts/generate-sbom.sh
```

## Repository map

| Path | Purpose |
|---|---|
| `src/jlink_mcp/` | MCP server, service, discovery, security, adapters, workflows, audit store |
| `firmware/giga_hil/` | Deterministic Arduino GIGA M7/M4 validation fixture |
| `tests/` | Unit, failure, container-security, GUI, and real-hardware suites |
| `container/` | Container entrypoint and pinned Arduino CLI configuration |
| `config/` | Restrictive host udev policy |
| `scripts/` | Bootstrap, hardware discovery, validation, udev, and SBOM helpers |
| `docs/` | Architecture, security, operations, validation, support, and licensing |
| `compose*.yaml` | Base runtime plus snap/noVNC/Ozone/SystemView profiles |
| `IMPLEMENTATION_PLAN.md` | Authoritative implementation and acceptance specification |
| `Agent.md` | Repository operating instructions for future AI engineering agents |

## Documentation

- [Architecture and tool layers](docs/architecture.md)
- [Security and trust boundaries](docs/security.md)
- [Installation and operations](docs/operations.md)
- [Hardware-in-the-loop validation](docs/hardware-validation.md)
- [Capability and support matrix](docs/support-matrix.md)
- [Licensing and proprietary boundary](docs/licensing.md)
- [Implementation and acceptance plan](IMPLEMENTATION_PLAN.md)
- [Third-party inventory](THIRD_PARTY_LICENSES.md)
- [CycloneDX SBOM](sbom/jlink-mcp.cdx.json)

## License

Repository-authored source is available under the
[PolyForm Noncommercial License 1.0.0](LICENSE). You may copy, modify, and
redistribute it for permitted noncommercial purposes. Sale, resale, and use in
a revenue-generating product, service, workflow, or business model are not
licensed. This makes J-Link MCP **source-available, not OSI open source**.

Third-party components retain their own terms. SEGGER software is installed
and licensed separately and is never distributed by this project. The J-Link
EDU Mini's non-profit educational-use restriction remains in force independently
of the repository license.
