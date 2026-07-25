# Arduino GIGA extension operations

Install its restrictive USB/serial policy and generate hardware environment
values:

```sh
extensions/arduino_giga/scripts/install-udev-rules.sh
extensions/arduino_giga/scripts/generate-hardware-env.sh
```

The rule confines matching Arduino USB nodes to `0660` and uses `plugdev` for
USB plus `dialout` for tty devices. Reconnect the board after installation.

Build the core image and maintained combined overlay:

```sh
docker compose --env-file .env.hardware build mcp
docker compose --env-file .env.hardware \
  -f compose.yaml -f compose.giga.yaml up --build -d
```

The overlay installs the pinned Arduino CLI/platform, both extension wheels,
bridge libraries, and explicitly enables `arduino_giga,giga_protocol_bridge`.
Use a mode-`0600` extension config for secrets; never commit it.

Start with doctor, capabilities, and `hardware_preflight`. Omitted profile/core
resolve to `arduino_giga_r1`/`m7` when this is the sole applicable bundle. Use
stable serials whenever hardware is changed.
