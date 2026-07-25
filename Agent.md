# J-Link MCP Agent Handbook

This file is the operating contract for AI engineering agents working in this
repository. Read it completely before changing code, running hardware tests,
or operating an attached target.

## Mission

Maintain J-Link MCP as a product-grade, capability-aware MCP server that lets
an AI agent develop, program, debug, test, and verify embedded firmware through
a SEGGER J-Link without unrestricted shell access or repeated human
intervention.

The authoritative scope and acceptance criteria are in
`IMPLEMENTATION_PLAN.md`. The product README describes the supported user
experience. When implementation, tests, documentation, and the plan disagree,
do not silently narrow the plan: reconcile the discrepancy and prove the
result.

## Non-negotiable boundaries

1. Never commit or place SEGGER binaries, firmware, SDK headers, manuals,
   Ozone, SystemView, or other proprietary artifacts in the repository or
   published image. Mount user-installed licensed products read-only.
2. Never use `--privileged`. Preserve the non-root user, dropped capabilities,
   read-only root filesystem, restricted device classes, and loopback-only MCP
   binding.
3. Never add a host-shell escape to raw Commander, GDB, application, or GUI
   tools. Keep executable allowlists, metacharacter rejection, path
   confinement, bounded input, and deterministic timeouts.
4. Never select a target by transient USB bus number, device address, or
   `ttyACM` number. Use stable probe and board serials. Automatic selection is
   allowed only for one unambiguous compatible pair.
5. Never perform a target-changing operation without positive live identity:
   selected J-Link serial, adequate VTref, SW-DP DPIDR, expected core CPUID,
   and target profile must agree.
6. Never assume the primary board is disposable. Back up readable flash before
   destructive HIL, restore it in `finally`, and verify the complete restored
   hash. Stop if backup fails unless the operator explicitly designated a
   separate sacrificial target with `TEST_TARGET_DISPOSABLE=true`.
7. Do not test irreversible readout protection, provisioning, option-byte
   programming, or mass erase on the primary GIGA. Use mocks or an explicitly
   designated sacrificial target.
8. Preserve the Arduino bootloader and programmed option/protection state.
   Transient runtime M4 release is acceptable; persistent option changes are
   not.
9. Keep every target/backend action structured and auditable. Results must
   retain exact commands, raw output, parsed values, timing, identities,
   states, hashes, warnings, and evidence paths where applicable.
10. Respect both license boundaries: repository-authored source is PolyForm
    Noncommercial 1.0.0 and cannot be sold or used for revenue-generating
    activity; the EDU Mini separately has SEGGER's qualifying non-profit
    educational-use restriction. Neither grants rights to SEGGER software.

## Repository map

- `src/jlink_mcp/server.py`: MCP resources/tools, annotations, and HTTP auth.
- `src/jlink_mcp/service.py`: identity gate, leases, audited atomic operations,
  sessions, and backend coordination.
- `src/jlink_mcp/workflows.py`: build, deploy, observe, debug, crash, RTT,
  backup/restore, validation, and reporting workflows.
- `src/jlink_mcp/discovery.py`: USB/tool discovery and capability manifest.
- `src/jlink_mcp/security.py`: raw command, GDB, argument, and path validators.
- `src/jlink_mcp/backends/`: Commander, GDB, serial, GUI, application, and SDK
  adapter contracts.
- `src/jlink_mcp/store.py`: SQLite audit hash chain, sessions, and artifacts.
- `firmware/giga_hil/`: deterministic M7/M4 test fixture and embedded manifest.
- `tests/`: unit/fake backends, container security, GUI acceptance, and GIGA
  HIL acceptance.
- `config/59-jlink-mcp.rules`: final group-based `0660` device policy loaded
  before Arduino's world-writable vendor rule.
- `scripts/`: bootstrap, host discovery, udev installation, validation, and
  software-composition evidence.
- `docs/`: architecture, operations, security, validation, support, licensing.
- `state/`: ignored runtime evidence. It belongs to the operator; do not delete
  it casually or treat it as source.

## Local environment

Work from the repository root. Prefer this PATH for project commands:

```sh
export PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH"
```

Create the development environment when needed:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

Local secrets and hardware configuration are `.token` and `.env.hardware`.
Never print the token, commit either file, or copy them into an image layer.
`scripts/bootstrap.sh` creates them and discovers the current host device
configuration.

The normal runtime command is:

```sh
docker compose --env-file .env.hardware up --build -d
```

On a Canonical snap Docker host use:

```sh
docker compose --env-file .env.hardware \
  -f compose.yaml -f compose.snap.yaml up --build -d
```

Use the snap overlay only when that runtime requires it. Standard Docker
Engine retains the stronger base `no-new-privileges` setting.

## Development rules

- Support Python 3.12 and keep the MCP SDK on the pinned 1.27.x contract until
  a deliberate migration updates code, lockfile, schemas, and regression
  coverage.
- Keep public contracts typed with Pydantic models. Reject unknown or
  ambiguous inputs rather than guessing.
- Build subprocess argv arrays directly. Never use `shell=True`.
- Confine caller-provided paths to the configured workspace or state roots,
  resolving symlinks before use.
- Use one exclusive lease per physical probe. Long-running GDB/GUI sessions
  hold the lease until explicit cleanup. Concurrency is only across probes.
- Terminate subprocess groups on timeout/cancellation and recover persisted
  stale sessions at startup.
- Make unsupported capabilities explicit with state, dependencies, and a
  useful reason. Do not silently degrade or claim hardware, wiring, license,
  or product support that was not observed.
- Mark MCP tool annotations conservatively and accurately. Any operation that
  can reset, halt, resume, write RAM/flash/registers, launch a target-owning
  session, or otherwise alter target behavior is mutating.
- Keep SEGGER-version parsing covered by golden output. A SEGGER upgrade is not
  accepted until headless, parser, GUI, OCR, and screenshot regressions pass.
- Derive GIGA flash/RAM regions from Arduino build metadata and ELF segments.
  Do not add unexplained hard-coded image partition addresses.
- Register generated ELF, HEX, BIN, map, symbols, disassembly, manifest, and
  checksums as MCP-managed artifacts.
- Preserve existing user changes and ignored runtime evidence. Avoid broad
  cleanup, reset, checkout, or deletion commands.

## Hardware workflow

Before a target-changing operation:

1. Call `get_capabilities` and inspect unavailable reasons.
2. Call `dependency_doctor` and resolve every required failure.
3. Call `hardware_preflight` with stable selectors.
4. Confirm expected CPUID values (`0x411FC271` M7 and `0x410FC241` M4), SW-DP
   `0x6BA02477`, and VTref of at least 1.0 V.
5. For restored user firmware that boot-holds M4, use
   `prepare_giga_dual_core_debug`; do not persistently alter boot options.

For destructive HIL, the test client must use MCP after initial container
startup. It must not call Commander, GDB Server, Arduino CLI, or serial tools
directly except for explicit failure-recovery diagnostics. Always preserve an
original full-flash backup and its SHA-256 until restoration is independently
verified.

Do not leave the fixture on the user's target. The final hardware state must be
the original firmware, verified byte-for-byte, reset to run, and reconnectable.

## Verification ladder

Use the narrowest useful test while iterating, then finish with the applicable
full gates.

```sh
# Focused/unit iteration
.venv/bin/python -m pytest tests/path_or_test.py -q

# Required source validation: compilation, unit/fakes, >=80% aggregate
# coverage, Compose configuration, SBOM/licenses, proprietary boundary
scripts/validate.sh

# Exact running image security
JLINK_MCP_CONTAINER_TEST=1 \
  .venv/bin/python -m pytest tests/container/test_security.py -v

# All advertised installed GUI applications through MCP
JLINK_MCP_GUI=1 \
  .venv/bin/python -m pytest tests/hil/test_gui_acceptance.py -v

# Full real-hardware build/flash/debug/fault/restore sequence
JLINK_MCP_HIL=1 \
  .venv/bin/python -m pytest tests/hil/test_giga_acceptance.py -v
```

Tests using `asyncio.to_thread` may require execution outside a restrictive
command sandbox if that sandbox blocks cross-thread event-loop wakeups. That
is an execution-environment limitation, not a reason to remove concurrency or
weaken the test.

After a source change that affects the container, rebuild and recreate the
service before container, GUI, or HIL acceptance. Check that it is healthy,
that `/healthz` returns 200, unauthenticated `/mcp` returns 401, and an
authenticated MCP client initializes successfully.

## Completion checklist

Do not claim a change complete from intent or a narrow green test. Match proof
to the changed scope and, for full product acceptance, verify all of these:

- The source-available image builds without any proprietary SEGGER artifact.
- The service is healthy, non-root, capability-free, read-only, unprivileged,
  loopback-bound, and bearer-authenticated.
- Base and optional Compose profiles validate.
- Dependency doctor reports all required host, container, toolchain, GUI,
  hardware, identity, voltage, license, workspace, and state checks.
- J-Link and Arduino USB/serial nodes use group-owned `0660`, not `0666`.
- Unit/parser/mock/failure suites pass with at least 80% aggregate coverage.
- Container-security and exhaustive installed-GUI suites pass.
- The real GIGA suite builds and verifies the custom M7/M4 firmware, exercises
  required debug/observation/fault paths, validates ELF/HEX/BIN, and passes.
- Bootloader and persistent option/protection state are preserved.
- The original 2 MiB flash hash matches the post-restore backup exactly and the
  board is reset to run.
- The SQLite audit chain verifies.
- Final JSON and Markdown reports contain hardware, dependencies, commands,
  logs, target states, hashes, screenshots, and evidence paths.
- SBOM and third-party license inventory are current.
- README, support matrix, operations, security, and validation documentation
  accurately describe the shipped behavior.

If any proof is missing or indirect, keep working or state the exact blocker.
Never redefine completion around what is easiest to test.
