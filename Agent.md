# AI agent operating contract

This is the canonical contract for repository work. Read it completely before
editing source, operating an attached probe/target, or running HIL. Nested
contracts add requirements within each first-party extension.

## Core and extension boundary

The root `jlink-mcp` package is target-neutral. Core may own probe discovery,
leases, Commander/GDB/RTT/SWO/serial/application/GUI primitives, generic
flash/verify/backup/compare/restore/ELF/report workflows, audit, security, and
the public extension API.

Board USB identities, target device names, CPUID/DPIDR expectations, SDK/build
settings, firmware images, target workflows, and physical-fixture procedures
belong to extensions. Do not add Arduino, GIGA, STM32H747, or protocol-bridge
implementation or dependencies to `src/jlink_mcp`, the root image/Compose
configuration, or the core SBOM. Root documentation may link to extensions but
must identify them as separately installed optional tools.

`extensions/arduino_giga` owns the GIGA target bundle.
`extensions/giga_protocol_bridge` owns the bridge and depends only on the
public core API plus public services published by `arduino_giga`. Do not import
private runtime internals from extensions.

Extension API changes require versioned compatibility reasoning, collision and
failure tests, and documentation updates. Extensions are trusted in-process
code and load only from the explicit allowlist.

## General engineering rules

- Preserve public MCP tool names and request schemas unless an intentional
  compatibility change is documented and approved.
- Fail closed on ambiguous identities, missing profiles, identity mismatch,
  unavailable dependencies, unsafe paths, or invalid extension configuration.
- Use stable serial selectors. USB bus/address and transient tty names are
  observations, never target keys.
- Keep target-changing operations behind positive identity validation,
  exclusive probe leases, audit records, and bounded timeouts.
- Never invoke a shell from MCP command data. Preserve argument allowlists and
  workspace/state path confinement.
- Keep SEGGER software user-supplied and read-only. Never commit, package,
  copy, or redistribute proprietary SEGGER artifacts.
- Preserve existing user changes and ignored evidence. Avoid broad cleanup,
  reset, checkout, or deletion commands.
- Use one workspace lock while keeping core and extension wheels independently
  buildable and installable.

## Hardware operations

Before any target-changing action call `dependency_doctor` and
`get_capabilities`, inspect required failures, then run the applicable
extension preflight with stable selectors. Use the target profile's identity
values; never substitute remembered constants in generic core logic.

Destructive HIL must preserve a full original backup and SHA-256 authorization
until restoration is independently verified. HIL clients operate through MCP
after service startup except for explicit failure-recovery diagnostics. Do not
leave test firmware on a user's target. The final state must be restored,
byte-verified, reset to run, reconnectable, and recorded in the audit chain.

Follow the nested extension contract for hardware-specific procedures:

- `extensions/arduino_giga/Agent.md`
- `extensions/giga_protocol_bridge/Agent.md`

## Verification ladder

Use focused tests while iterating, then run gates proportional to the change:

```sh
.venv/bin/python -m pytest tests/path_or_test.py -q
.venv/bin/python -m pytest tests --ignore=tests/container -q
.venv/bin/python -m pytest extensions/arduino_giga/tests -q
.venv/bin/python -m pytest extensions/giga_protocol_bridge/tests -q
scripts/validate.sh
```

For container changes, rebuild the exact core image and applicable overlay,
validate Compose, then run container security. For extension distribution
changes, build and inspect all three wheels. Hardware acceptance is opt-in and
must use the extension's documented backup/restore procedure.

Do not claim completion from a narrow test. Report unavailable physical
fixtures and skipped gates precisely.

## Commit messages

Every agent-authored commit must have a concise imperative subject, a blank
line, and a detailed body. A subject line by itself is not acceptable,
including for small changes.

The body must accurately describe:

- the problem or intended outcome and the major implementation areas changed;
- important design, compatibility, security, generated-artifact, dependency,
  or operational decisions;
- the exact validation performed and its result, including hardware backup,
  restore, hash, and operation evidence when applicable; and
- remaining limitations, unavailable physical fixtures, skipped checks, or
  follow-up work without presenting unverified behavior as complete.

Write the message from the final staged diff, not the original request or an
earlier plan. Keep the subject useful in short history views and leave enough
body context for a future maintainer to understand what shipped and how it was
proven without reconstructing the agent session.
