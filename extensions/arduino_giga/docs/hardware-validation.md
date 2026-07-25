# Arduino GIGA hardware validation

The destructive suite is
`extensions/arduino_giga/tests/hil/test_giga_acceptance.py`. It runs only with
`JLINK_MCP_HIL=1` and operates through the MCP endpoint after startup.

Before enabling it:

1. Run doctor/capabilities and resolve required failures.
2. Run `hardware_preflight` using the exact probe and board serials.
3. Confirm M7/M4 CPUID, SW-DP ID, and VTref defined in `Agent.md`.
4. Ensure the 2 MiB full-flash backup can be retained in persistent state.

The suite snapshots persistent registers, backs up and hashes original flash,
builds and verifies both fixture images, exercises serial/RTT/debug/fault paths,
then restores and byte-verifies the complete backup in `finally` handling. It
must end with original firmware running, the board reconnectable, preserved
boot/protection state, and a valid audit chain.

```sh
JLINK_MCP_HIL=1 \
  .venv/bin/python -m pytest \
  extensions/arduino_giga/tests/hil/test_giga_acceptance.py -v
```

Installed SEGGER GUI acceptance is separately enabled with `JLINK_MCP_GUI=1`.
Report a skipped physical check as unavailable; never weaken restoration or
identity assertions to make a fixture pass.
