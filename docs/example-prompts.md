# Example engineering prompts

These examples show how an engineer can ask an MCP-capable agent to use J-Link
MCP. Each prompt is followed by a possible answer based on a real Arduino GIGA
R1 and J-Link EDU Mini V2 demonstration performed on 2026-07-24 EDT
(2026-07-25 UTC).

The answers are evidence snapshots, not values that every board should return.
Probe and board serials are deliberately omitted from this public document.
Always use the stable serials returned by `get_capabilities` for the attached
hardware.

The demonstration used the small dual-core fixture in
[`firmware/giga_hil`](../firmware/giga_hil/README.md). It preserved the
existing firmware with a complete 2 MiB backup, deployed and exercised the
fixture, restored the backup, and independently verified the restored hash.

## Discover and positively identify the hardware

### Example prompt

> Use `jlink_mcp`. Run `get_capabilities` and `dependency_doctor`, then run
> `hardware_preflight` with the returned stable probe and board serials. If M4
> is boot-held, use the transient dual-core preparation. Do not change flash or
> option bytes. Summarize the probe, target identities, VTref, required
> failures, and optional limitations.

### Possible answer from the demonstration

> One unambiguous J-Link/GIGA pair was detected. All required dependency,
> device-permission, license, container-security, and target-identity checks
> passed. The M7 identified as Cortex-M7 with CPUID `0x411FC271`; the M4
> identified as Cortex-M4 with CPUID `0x410FC241`. Both reported SW-DP DPIDR
> `0x6BA02477` and VTref near `3.284 V`.
>
> The restored firmware initially boot-held M4. Transiently setting
> `RCC_GCR.BOOT_C2` from `0x00000000` to `0x00000008` made M4 attachable
> without changing flash or option bytes.
>
> Dual-core deployment, managed debugging, serial, RTT, backup/restore, and
> validation workflows were available. Optional Ozone, SystemView, and J-Link
> SDK integrations were unavailable; the EDU Mini cannot power the target or
> capture ETM trace, and physical SWO wiring remained unverified.

## Back up, build, deploy, and verify a dual-core fixture

### Example prompt

> Back up the full GIGA flash at `0x08000000` for `0x200000` bytes and retain
> its SHA-256. Only if the backup succeeds, build and deploy
> `firmware/giga_hil/m7` and `firmware/giga_hil/m4` with the certified `75_25`
> split. Report build identity, image address, size, CRC-32, artifact hashes,
> and byte-verification status. Restore the original backup in a `finally`
> path.

### Possible answer from the demonstration

> The 2 MiB backup succeeded with SHA-256
> `3a1e7fe194864389d2adbde3cc4036a9642c08e682b1cb9d9653d94a1fa10851`
> (operation `50de0280-5e5b-41c9-828e-ea2d9383fd60`).
>
> Both images were built from Git commit
> `40eed5afa0a241fb84f8ece870c76c934df0e8a7`:
>
> | Core | Address | Image size | Image CRC-32 | BIN SHA-256 |
> |---|---:|---:|---:|---|
> | M7 | `0x08040000` | 171,012 bytes | `0x847321AD` | `7ba46317dc283c1796f126d0dd1bca7f719a2c69491a98a7d501db964be88372` |
> | M4 | `0x08180000` | 139,856 bytes | `0xECFA5E80` | `c1a3b9835fb8c394fa30a3415c8dacf0249722781cedff6fb7e1804e58ec649b` |
>
> ELF, HEX, BIN, map, symbols, disassembly, manifest, and checksum artifacts
> were registered. Manifest size and CRC verification passed for both cores,
> and J-Link byte verification passed after each flash operation.

## Validate live firmware over USB serial and memory

### Example prompt

> Reset and run both fixture cores. Over the board serial associated with the
> stable board identity, request `PING`, `MANIFEST`, `INFO`, `SELFTEST`, and
> `RPC`. Confirm that both memory heartbeat counters advance and summarize the
> returned structured records.

### Possible answer from the demonstration

> The fixture returned `pong` from M7. Its manifest reported magic
> `JLINK_MCP_HIL`, protocol 1, build ID
> `20260725T032218Z-81b7cca0`, image size 171,012, and the expected source
> commit.
>
> `INFO` reported M7 heartbeat 67, uptime 16,768 ms, watch value 305,419,896
> (`0x12345678`), and RAM CRC 2,090,680,779. `SELFTEST` returned `ok: true`
> with the same CRC. Cross-core `RPC` returned a live M4 heartbeat of 308.
>
> Independent SWD memory reads showed M7 heartbeat `0x92` advancing to `0xA2`
> and M4 heartbeat `0x1C7` advancing to `0x1F0`.

## Debug a live source-level problem

### Example prompt

> Using the deployed M7 ELF, demonstrate a symbolic breakpoint, stack trace,
> SP/LR/PC reads, a hardware watchpoint, instruction and source stepping, and a
> bounded RAM write/read verification. Clean up the managed debug session and
> leave the target running. Return the most useful source locations and state
> changes.

### Possible answer from the demonstration

> The managed debugger resolved `jlink_mcp_breakpoint_site` to `m7.ino:17` at
> `0x08040370` and stopped there. The captured stack was:
>
> 1. `jlink_mcp_breakpoint_site`
> 2. `handle_command`
> 3. `loop`
> 4. `main`
>
> At the breakpoint, SP was `0x2400C308`, LR was `0x08040CAB`, and PC was
> `0x08040370`. A hardware watchpoint caught `jlink_mcp_watch_value` changing
> from 305,419,897 to 2,779,077,210 (`0xA5A55A5A`). Instruction stepping
> advanced PC from `0x08049C7A` to `0x08049C7C`.
>
> A bounded test-buffer write was read back exactly as
> `00112233445566778899aabbccddeeff`. The debug assertion returned `ok: true`;
> its managed session ID was `72ad6be2-de88-4627-96b5-45e0357ae3e0`.

## Capture telemetry without consuming the application serial port

### Example prompt

> Inspect the M7 ELF for `_SEGGER_RTT`, derive its control-block address, and
> capture RTT channel 0 for two seconds. Report whether boot, manifest,
> heartbeat, self-test, and cross-core RPC records are present, and register a
> hashed log artifact.

### Possible answer from the demonstration

> `_SEGGER_RTT` was derived at `0x24000004`. The two-second capture succeeded
> and collected 1,192 bytes containing boot, pong, manifest, info, heartbeat,
> self-test, and M4 RPC records. The log artifact SHA-256 was
> `88e9f21f8a865d57edfcea3c7e81658abaedc767d305f1a0f9d0f70e16540506`;
> capture operation `f536be76-ed76-4514-b127-5749998afea8` completed without a
> timeout.

## Capture and recover a controlled crash

### Example prompt

> Use the fixture's controlled fault command to reproduce a HardFault. Set a
> symbolic `HardFault_Handler` breakpoint first, then collect the halt reason,
> stack, R0-R3, R12, SP, LR, PC, xPSR, and 128 bounded stack bytes. Recover by
> reset and run, and confirm that the workflow completed successfully.

### Possible answer from the demonstration

> The firmware emitted `{"event":"fault-enter","core":"m7"}` and stopped at
> `HardFault_Handler` (`0x0804F612`) with halt reason `breakpoint-hit`. The
> captured registers included R2 `0xBAD00BAD`, SP `0x2407FF78`, LR
> `0xFFFFFFED`, and PC `0x0804F612`; 128 stack bytes were retained. A
> HardFault frame was detected, and the workflow reset and resumed the target.
> The crash-capture result returned `ok: true`.

## Prove restoration and evidence integrity

### Example prompt

> Before restoring, compare the original backup's first `0x40000` bytes with
> target flash to prove the bootloader region was preserved. Restore the
> complete backup using its expected SHA-256, reset to run, read back all 2 MiB
> into a new artifact, compare the two hashes, verify the audit chain, and
> generate JSON and Markdown validation reports.

### Possible answer from the demonstration

> The protected 256 KiB bootloader region matched the original backup before
> restoration. The hash-authorized restore programmed and verified the full
> image, then reset the target to run (restore operation
> `5c49ce2b-f6de-4751-993f-e5cf6ea63412`).
>
> An independent post-restore 2 MiB readback produced the original SHA-256
> `3a1e7fe194864389d2adbde3cc4036a9642c08e682b1cb9d9653d94a1fa10851`,
> proving byte-for-byte restoration. The audit chain returned `ok: true`.
>
> Validation report run `2c360a85-f4b4-41f6-b067-e4d8be4d8100` produced a
> JSON report with SHA-256
> `7912e3404336f29b525497ef4d14bf6e5d5fe7e07fd274b82419927129aa0bc6`
> and a Markdown report with SHA-256
> `acf42e8369c62490ef7309efb3a83232a4fe513d5fba44c0901d5f203bb8ba20`.

## Recommended all-in-one prompt

> Use `jlink_mcp` to demonstrate the GIGA fixture safely. Start with
> `get_capabilities`, `dependency_doctor`, and positive dual-core
> `hardware_preflight` using stable serials. Back up all 2 MiB of flash and
> stop if that fails. In a protected `try/finally`, build and deploy the M7/M4
> fixture, verify manifests and image bytes, observe serial self-test and RPC,
> prove both heartbeats advance, demonstrate managed source debugging, capture
> RTT, and capture/recover a controlled HardFault. In `finally`, restore the
> original hash-authorized backup. Independently read back all flash and
> require its SHA-256 to equal the original. Verify the audit chain and return
> concise results with operation IDs and evidence paths.
