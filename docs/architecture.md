# Architecture

## Target-neutral runtime

```text
MCP client
    | loopback HTTP + bearer token
    v
FastMCP core tools/resources
    v
JLinkService: selector resolution + identity gate + lease + audit
    +-- Commander / GDB / RTT-SWO / serial
    +-- SEGGER application and GUI adapters
    +-- generic artifacts and workflows
    |
    +-- ExtensionRegistry
          +-- target profiles and board detectors
          +-- tools and resources
          +-- capability/doctor providers
          +-- published extension services
    v
J-Link probe --> extension-defined target
```

Core starts with no target profile. It can enumerate probes and report tools,
but a target operation fails clearly until an enabled extension registers a
profile. Profiles define core IDs, J-Link device names, expected core/CPUID,
DPIDR, voltage floor, interface, speed, and default core. Board detectors turn
generic USB observations into profile-associated boards.

## Extension lifecycle

Installed entry points in `jlink_mcp.extensions` are inert unless their IDs are
listed in `JLINK_MCP_EXTENSIONS`. The loader validates identity and API version,
resolves declared dependencies topologically, validates mode-`0600` TOML plus
environment overrides with the extension's Pydantic model, then calls
`register(context)`. Shutdown runs in reverse dependency order.

`ExtensionContext` is the registration boundary. It detects duplicate tools,
resources, profiles, detector IDs, and published services. Stable services
cover J-Link operations, serial transport, artifacts, audit, paths, and bounded
process execution. Extensions do not receive `MCPRuntime` internals.

## Identity, concurrency, and evidence

USB bus/address and tty names are transient observations. Selection uses stable
probe and board serials plus a registered target profile. Every changing action
reads live profile-defined identity, acquires one exclusive lease per probe,
and records both accepted and rejected operations.

`CommandResult` carries operation/session IDs, exact argv or debugger command,
timing, return/timeout state, raw and parsed output, target states, identities,
hashes, warnings, and evidence paths. SQLite operations are SHA-256 chained;
artifacts and sessions share the persistent state store.

## Distribution boundary

The root wheel and image contain only core code. First-party extensions are
independent wheels under `extensions/`. The workspace has one lock for
reproducibility. The maintained GIGA overlay layers the two extension wheels,
board toolchain, firmware assets, and physical-device configuration onto the
core image.
