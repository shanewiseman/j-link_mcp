# Arduino GIGA extension agent contract

Read the root `Agent.md` first. This extension exclusively owns Arduino GIGA
discovery, target identity, build/toolchain settings, dual-core workflows,
firmware fixtures, device rules, container layer, and GIGA HIL.

Keep GIGA constants and dependencies here. Preserve the profile ID
`arduino_giga_r1`, core IDs `m7`/`m4`, and existing MCP tool request schemas.
Derive image layout from Arduino build metadata and ELF segments; do not add
unexplained partition addresses. Register ELF, HEX, BIN, symbols,
disassembly, manifests, and checksums as audited artifacts.

Before changing the attached target, call `dependency_doctor`,
`get_capabilities`, and `hardware_preflight` with stable probe and board
serials. Confirm VTref is at least 1.0 V, SW-DP is `0x6BA02477`, M7 CPUID is
`0x411FC271`, and M4 CPUID is `0x410FC241`. A boot-held M4 may be released
transiently with `prepare_giga_dual_core_debug`; do not persistently alter boot,
option, or protection state.

Destructive HIL must back up and hash the full 2 MiB flash before programming.
After validation, restore the original bytes, independently verify the full
hash, confirm preserved boot/protection registers, reset both cores to run,
reconnect, and verify the audit chain. Never leave fixture firmware installed.
The HIL client uses MCP after service startup.

Run extension unit/fixture tests, wheel inspection, GIGA overlay build, doctor,
and available HIL proportional to the change. Record exact backup/restore
hashes and operation evidence in the detailed commit body required by the root
contract.
