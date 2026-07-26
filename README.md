# J-Link MCP

J-Link MCP is a target-neutral Model Context Protocol server for SEGGER J-Link
debug probes. It gives an MCP client typed, audited access to probe discovery,
target state, memory and registers, flash/verify/backup/restore, managed GDB,
RTT/SWO, serial channels, installed SEGGER applications, and optional GUI
automation.

The core does not assume a microcontroller, board, SDK, or firmware image.
Target profiles and hardware workflows arrive through explicitly enabled,
trusted Python extensions.

## Packages

| Package | Extension ID | Purpose |
|---|---|---|
| `jlink-mcp` | — | Target-neutral runtime, services, tools, audit, and core container |
| [`jlink-mcp-arduino-giga`](extensions/arduino_giga/README.md) | `arduino_giga` | Optional Arduino GIGA discovery, profile, builds, dual-core workflows, fixtures, and HIL |
| [`jlink-mcp-giga-protocol-bridge`](extensions/giga_protocol_bridge/README.md) | `giga_protocol_bridge` | Optional universal protocol-bridge firmware and tools; depends on `arduino_giga` |

The protocol bridge is a separately installed optional tool, not a core MCP
feature. See the [extension author guide](docs/extensions.md) for the public API.

## Core quick start

Requirements are Linux x86-64, Docker Compose v2, a locally licensed SEGGER
J-Link Software Pack, a J-Link probe, and membership in `docker` and `plugdev`.
SEGGER files are never copied into the image.

```sh
scripts/install-udev-rules.sh
scripts/bootstrap.sh
docker compose --env-file .env.hardware up --build -d
curl --fail http://127.0.0.1:8000/healthz
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. Every request needs the bearer
token in `.token`. The core starts with `JLINK_MCP_EXTENSIONS` empty, so target
operations intentionally fail until an enabled extension registers a target
profile.

For a local Python workspace:

```sh
uv sync --frozen --package jlink-mcp --extra test --extra gui
.venv/bin/jlink-mcp serve
```

Start every hardware session with `dependency_doctor` and `get_capabilities`.
Use stable probe/board serials and retain operation IDs as evidence.

### Connected GIGA demonstration

With the maintained GIGA deployment running, use this demonstration to prove
that MCP, J-Link Commander, the attached probe, and both STM32H747 cores can be
identified:

```sh
.venv/bin/python examples/giga_connection_demo.py
```

The example does not erase, program, or write firmware. It runs the required
doctor and capability checks, resolves the unique probe/board pair to stable
serial selectors, enumerates the J-Link, and validates GIGA voltage, DPIDR,
M7/M4 CPUID values, and an option/register snapshot. If the M4 is boot-held,
the preflight transiently releases it through `RCC_GCR.BOOT_C2`; it does not
change persistent boot, option, protection, or firmware state. Successful
output includes operation IDs for the corresponding audit evidence.

## Extension activation

Only comma-separated IDs in `JLINK_MCP_EXTENSIONS` load. An optional
mode-`0600` TOML file selected by `JLINK_MCP_EXTENSION_CONFIG` holds namespaced
configuration:

```toml
[extensions.example]
mode = "safe"
```

Environment overrides use
`JLINK_MCP_EXT_<NORMALIZED_ID>__<FIELD>`. Extensions run as trusted in-process
Python code: the allowlist controls activation, not isolation.

The maintained GIGA compatibility image installs and enables both first-party
extensions:

```sh
extensions/arduino_giga/scripts/install-udev-rules.sh
extensions/arduino_giga/scripts/generate-hardware-env.sh
docker compose --env-file .env.hardware \
  -f compose.yaml -f compose.giga.yaml up --build -d
```

Hardware-safe backup, validation, and restoration procedures live in the
[Arduino GIGA extension documentation](extensions/arduino_giga/docs/hardware-validation.md).

## Hardware-free validation

Bootstrap the locked workspace once, then run the complete native validation
without Docker, SEGGER software, a probe, or a target:

```sh
scripts/ci-bootstrap.sh
scripts/validate-native.sh
```

The native gate checks Ruff and the frozen lock, runs the core and both
first-party extension suites with independent 80% branch-coverage thresholds,
generates JUnit and Cobertura reports under `artifacts/`, validates CycloneDX
inventories and all three wheels, and enforces core-neutrality and proprietary
artifact boundaries. Jenkins consumes the same commands from
`.jenkins/pipeline.yaml` and permits egress only during the bootstrap step.

## Safety and evidence

Target-changing calls resolve a registered profile, select stable identities,
acquire an exclusive probe lease, and positively validate profile-defined
VTref, DP ID, core, and CPUID before executing. Raw Commander/GDB/application
surfaces are bounded, shell-free, and path-confined. Results include exact
commands, state, identity, hashes, timestamps, and evidence paths; SQLite audit
entries form a SHA-256 chain.

The container is loopback-bound, bearer-authenticated, non-root,
capability-free, read-only except for explicit workspace/state mounts, and
uses a user-supplied read-only SEGGER installation. Details are in
[security](docs/security.md), [operations](docs/operations.md), and the
[support matrix](docs/support-matrix.md).

## License

Repository-authored work is licensed under PolyForm Noncommercial 1.0.0 with
the additional notices in [LICENSE](LICENSE). The project is source-available, not OSI open source.
Using it in a revenue-generating product, service, workflow, or business model requires a
commercial license from the licensor.
Third-party and user-supplied components retain their own terms; see
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
