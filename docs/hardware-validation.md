# GIGA hardware-in-the-loop validation

## Safety invariants

The suite must back up readable application flash before programming and must
restore it in `finally`. If a backup is impossible, validation stops unless the
operator explicitly sets `TEST_TARGET_DISPOSABLE=true` for a sacrificial
target. The primary GIGA is never used for irreversible protection,
provisioning, option-byte, or mass-erase tests.

Record before/after DBGMCU ID, both option status/program registers, write
protection registers, boot registers, and a bootloader-region hash. A transient
runtime BCM4 state is not a persistent option change; current and programmed
option values must agree after the test.

## Automated test layers

```sh
# Unit, parser, fake backend, security, workflow, and failure tests
.venv/bin/python -m pytest --cov=jlink_mcp --cov-report=term-missing

# Running-container security assertions
JLINK_MCP_CONTAINER_TEST=1 .venv/bin/python -m pytest tests/container -v

# GUI operations through MCP
JLINK_MCP_GUI=1 .venv/bin/python -m pytest tests/hil/test_gui_acceptance.py -v

# Full destructive GIGA sequence through MCP with guaranteed restoration
JLINK_MCP_HIL=1 .venv/bin/python -m pytest tests/hil/test_giga_acceptance.py -v
```

Set `JLINK_MCP_URL`, `JLINK_MCP_TOKEN_FILE`, `JLINK_MCP_PROBE_SERIAL`, and
`JLINK_MCP_BOARD_SERIAL` when defaults do not apply. The HIL test itself does
not invoke Docker, Commander, Arduino CLI, GDB, or serial utilities; after
container startup it is only an MCP client.

## Acceptance sequence

The HIL client verifies the complete typed tool surface, calls hardware
preflight, identifies both cores and VTref, snapshots option/protection/boot
state, backs up all 2 MiB of flash, and hashes it. It builds both repository
fixtures through MCP, checks source identity and embedded manifest
verification, programs M4 then M7 through the M7 access port, and verifies the
bytes.

It then proves serial manifest/heartbeat/self-test/M4 RPC behavior, memory
heartbeats, RTT, symbolic breakpoint, semantic watchpoint, registers, locals,
stack, instruction/source stepping, and exact designated-RAM modification. It
captures optional SWO/ITM or records the precise physical dependency, triggers
a controlled HardFault, captures context/backtrace/stack, resets, and recovers.
ELF, HEX, and explicit-address BIN inputs are each programmed and verified.
Disconnect/reconnect uses stable serials.

Finally, the suite restores the pre-test full-flash image through MCP, creates
a second 2 MiB backup, requires an identical SHA-256, checks reconnect/boot
behavior, verifies the audit chain, and emits JSON/Markdown reports. Any test
exception still enters restoration.

Timeout/cancellation, hung/crashed processes, stale sessions, multiple-device
ambiguity, USB renumbering, protection paths, and unavailable products are
also exercised by deterministic fakes so unsafe states need not be induced on
the primary board.
