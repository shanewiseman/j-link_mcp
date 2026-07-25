# Operations

## Core service

Run `scripts/bootstrap.sh`, then start `compose.yaml`. It mounts a local SEGGER
installation read-only, the USB bus, workspace, persistent state, and bearer
token. Core Compose enables no extensions and no board serial devices.

```sh
docker compose --env-file .env.hardware up --build -d
docker compose ps
curl --fail http://127.0.0.1:8000/healthz
```

Begin with `dependency_doctor` and `get_capabilities`. Resolve required failures
before target operations. Specify a stable probe serial when more than one
probe exists. A target also requires an installed and enabled target-profile
extension.

Persistent evidence is under `state/`: the audit database, command files,
artifacts, screenshots, reports, and SEGGER settings. Back it up according to
your evidence-retention policy. Never publish `.token`, extension secrets, or
state artifacts by default.

## Extensions

Set `JLINK_MCP_EXTENSIONS` explicitly and, when needed, provide a mode-`0600`
configuration file. Startup fails rather than ignoring a missing package,
dependency, invalid field, collision, or initialization error.

The maintained GIGA deployment uses `compose.giga.yaml`; follow the
[extension operations guide](../extensions/arduino_giga/docs/operations.md).
The optional bridge has additional secret-profile and physical-fixture rules in
its [canonical guide](../extensions/giga_protocol_bridge/docs/protocol-bridge.md).

## Upgrade and recovery

Rebuild/recreate after source, lock, image, or extension changes. Verify health,
unauthenticated MCP rejection, authenticated initialization, tool inventory,
and doctor output. Stale managed sessions are cleared and audited at startup.
Do not terminate unknown host debugger processes; this service owns only the
processes and leases it created.
