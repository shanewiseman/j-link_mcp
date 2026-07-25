# Target-Neutral Core and Extension Architecture

## Status

Implemented on the `swiseman/feature/genericCommFirmwareTool` line as the
follow-up to the universal protocol-bridge feature.

## Distribution boundaries

- `jlink-mcp` is the target-neutral core wheel and image. It owns probe access,
  Commander/GDB/serial/GUI adapters, audit storage, generic workflows, and the
  versioned extension API.
- `jlink-mcp-arduino-giga` owns GIGA discovery, target profiles, Arduino build
  assets, dual-core workflows, firmware fixtures, udev policy, and HIL tests.
- `jlink-mcp-giga-protocol-bridge` depends on the GIGA extension and owns the
  bridge protocol, backend, firmware, checked release, pinned-library inventory,
  and physical-fixture tests.

Extensions are installed through the `jlink_mcp.extensions` entry-point group
and remain inert unless explicitly allowlisted by `JLINK_MCP_EXTENSIONS`. The
loader enforces API version 1, dependency order, configuration validation,
collision checks, and reverse-order shutdown.

## Acceptance gates

- The core package, image, Compose manifest, SBOM, and license inventory contain
  no target-specific implementation.
- All three wheels build and install independently, with distribution-local
  entry points and assets.
- Core-only runtime registration is exactly 51 tools with no target profiles.
- The combined first-party bundle preserves the 14 pre-refactor GIGA and bridge
  tool names and request/response schemas.
- Unit, integration, container-security, distribution, and SBOM checks pass.
- Available hardware validation runs only after capability discovery, doctor,
  stable-serial selection, and positive M7/M4 identity preflight. Destructive
  acceptance backs up and byte-verifies restoration of the original flash.

See [Agent.md](Agent.md), [the architecture](docs/architecture.md), and
[the extension author guide](docs/extensions.md) for the canonical operating
contract and public API.
