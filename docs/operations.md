# Installation and operations

## One-time host preparation

1. Install Docker Engine/Compose on Linux x86-64 and SEGGER J-Link Software and
   Documentation Pack 9.62 for x86-64.
2. Add the user to `docker`, `plugdev`, and `dialout`; log out/in if membership
   changed.
3. Connect GIGA USB power/data. Wire J-Link VTref, GND, SWDIO, SWCLK, nRESET,
   and optionally SWO. The EDU Mini cannot power the target.
4. Install the restricted udev rules and reconnect devices:

   ```sh
   scripts/install-udev-rules.sh
   ```

   The installer uses `59-jlink-mcp.rules` with final `0660` assignments so
   an Arduino package's later `MODE:="0666"` rule cannot make the devices
   world-writable. It also removes the obsolete `99-jlink-mcp.rules` filename.
   It verifies every matching live node after reloading udev. Expected device
   mode is 0660 with `plugdev` (USB) or `dialout` (TTY).

5. Generate `.token`, local state, and `.env.hardware`:

   ```sh
   scripts/bootstrap.sh
   ```

`SEGGER_ROOT` defaults to `/opt/SEGGER/JLink_V962`. Override it before
bootstrap if needed. Never copy SEGGER files into the repository.

## Start and inspect

Standard Docker Engine:

```sh
docker compose --env-file .env.hardware up --build -d
docker compose --env-file .env.hardware ps
docker compose --env-file .env.hardware logs --tail=100 mcp
```

Snap Docker exception:

```sh
docker compose --env-file .env.hardware \
  -f compose.yaml -f compose.snap.yaml up --build -d
```

The health endpoint should return `{"status":"ok"}`. The MCP endpoint without
the correct bearer token returns 401. Use `jlink-mcp stdio-proxy --url
http://127.0.0.1:8000/mcp` for stdio-only clients.

## First MCP calls

Call `dependency_doctor`, `get_capabilities`, and `hardware_preflight` first.
The doctor distinguishes required failures from optional warnings and reports
Docker/cgroup, token, tools, Arduino assets, GUI runtime, device modes, probe,
board, live M7/M4 identities, voltage, and licenses. A missing optional SWO
wire, Ozone, SystemView, or SDK is a structured unavailable capability—not a
silent fallback.

If restored GIGA firmware intentionally holds CM4 under the BCM4 option
policy, call `hardware_preflight` with `prepare_dual_core=true` (or call the
lower-level `prepare_giga_dual_core_debug` workflow). It positively identifies
M7, transiently sets `RCC_GCR.BOOT_C2`, and then positively identifies M4. It
does not alter flash or option bytes.

Use the stable selector returned by discovery for subsequent calls. Do not
hard-code USB bus numbers or `/dev/ttyACM0`.

## Protocol bridge operations

Use `build_protocol_bridge_release` to regenerate the bridge bundle in managed
state and compare its HEX with the checked-in release. `deploy_protocol_bridge`
performs positive M7 identity checks, a mandatory complete flash backup,
flash/verify/reset, and the version/source handshake. Retain its backup
artifact until `restore_flash_backup` verifies the complete original hash.

After deployment, call `get_protocol_bridge_status`, then typed
`protocol_bridge_control`, `protocol_bridge_exchange`, and
`protocol_bridge_receive` requests. Named Wi-Fi/BLE secrets require a
container-visible mode-`0600` file selected by
`JLINK_MCP_BRIDGE_PROFILES_FILE`. See [the protocol bridge guide](protocol-bridge.md)
for wiring, roles, payload/queue formats, examples, and recovery.

## GUI diagnostics

GUI programs run inside Xvfb. To inspect the isolated display through noVNC:

```sh
docker compose --env-file .env.hardware \
  -f compose.yaml -f compose.novnc.yaml up -d
```

noVNC remains loopback-only at port 6080. Normal automation uses semantic
AT-SPI state, then xdotool, OCR, and version-pinned OpenCV templates as
fallbacks. Screenshots are stored under `state/screenshots` and audited.

Optional separately licensed products use explicit read-only overlays:

```sh
OZONE_ROOT=/opt/SEGGER/Ozone docker compose --env-file .env.hardware \
  -f compose.yaml -f compose.ozone.yaml up -d
SYSTEMVIEW_ROOT=/opt/SEGGER/SystemView docker compose --env-file .env.hardware \
  -f compose.yaml -f compose.systemview.yaml up -d
```

## Updates and shutdown

Run the complete unit suite and parser/GUI regressions before accepting a
SEGGER upgrade. Version 9.62 is the certified baseline. Stop gracefully with:

```sh
docker compose --env-file .env.hardware down
```

State and reports persist under `state/`. Removing that directory deletes
audits, backups, and restoration evidence; archive it first.

## Troubleshooting

- No probe/board: inspect `lsusb`, group membership, and udev modes, then
  reconnect. Discovery waits through transient renumbering.
- Permission denied: rerun the udev installer; do not use `--privileged` or
  world-writable permanent device rules.
- Target identity failure: verify wiring, target power/VTref, selected core,
  and stable serial. Never override the gate.
- GDB busy: stop the recorded GDB/GUI session or wait for its lease timeout.
- Missing RTT: confirm the ELF contains `_SEGGER_RTT` and use `capture_rtt`,
  which supplies the derived address to the logger.
- Missing SWO data: verify the physical SWO wire and keep the EDU Mini sample
  rate at or below 4 MHz.
- Snap `operation not permitted` at initial exec: use only the supplied snap
  overlay or migrate to standard Docker Engine.
- Bridge profile rejected: verify the exact path is visible inside the
  container, is a regular file, and has mode `0600`.
- Bridge exchange timeout: confirm the checked-in firmware handshake, USB-C
  control connection, stable board serial, selected M7 core, and the physical
  companion wiring. Status cannot prove an external peripheral is present.
