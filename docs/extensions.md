# Extension author guide

Extension API version 1 lets a trusted Python distribution add target knowledge
and tools without coupling it to the core package.

## Package and entry point

Declare a unique entry point whose name equals the extension object's `id`:

```toml
[project.entry-points."jlink_mcp.extensions"]
example_target = "example_extension:extension"
```

The exported object implements:

```python
from pydantic import BaseModel, ConfigDict
from jlink_mcp.extensions import EXTENSION_API_VERSION


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    greeting: str = "hello"


class ExampleExtension:
    id = "example_target"
    version = "1.0.0"
    api_version = EXTENSION_API_VERSION
    dependencies = ()
    config_model = Config

    def register(self, context):
        @context.register_tool()
        def example_greeting(name: str) -> str:
            return f"{context.config.greeting}, {name}"

    def shutdown(self):
        return None


extension = ExampleExtension()
```

Only IDs in `JLINK_MCP_EXTENSIONS` activate. Installed extensions are trusted
in-process Python code; activation is not a sandbox or permission boundary.

## Registration surface

Use only `ExtensionContext` methods:

- `register_tool` and `register_resource` add MCP surfaces.
- `register_target_profile` adds profile-defined cores and identity rules.
- `register_board_detector` maps generic USB observations to boards.
- `register_capability_provider` merges tools, workflows, features,
  limitations, raw surfaces, and atomic tool names.
- `register_dependency_provider` adds uniquely named doctor checks.
- `publish_service` and `require_extension_service` provide an explicit
  dependency-ordered extension-to-extension contract.

`context.services` exposes stable J-Link, serial, artifact, audit, path, and
process services. Treat all other runtime objects as private.

Registration fails on duplicate extension IDs, tools, resource URIs, profiles,
detectors within one extension namespace, capability keys, dependency checks,
or services. It also fails for missing/disabled dependencies, cycles,
unsupported API versions, invalid configuration, unsafe config modes, import
errors, and initialization failures.

## Target profiles

A `TargetProfile` declares a unique ID, display name, mapping of string core IDs
to `CoreProfile`, a default core, expected SW-DP ID, voltage floor, defaults,
and optional metadata. Each core declares its J-Link device, expected reported
core, CPUID, optional SVD name, and metadata. Put every target constant here,
not in generic core code.

A detector receives a generic `USBDevice` and either returns
`BoardCapabilities` associated with the profile or `None`. Two matching
detectors are treated as an ambiguity and fail closed.

## Configuration

Select one mode-`0600` TOML file with `JLINK_MCP_EXTENSION_CONFIG`:

```toml
[extensions.example_target]
greeting = "ready"
```

Override a field with
`JLINK_MCP_EXT_EXAMPLE_TARGET__GREETING=ready`. Double underscores address
nested fields. Values are JSON-decoded when possible, and the extension's
Pydantic model rejects unknown or invalid fields.

## Compatibility and testing

Keep tool names, descriptions, annotations, request types, defaults, and
response models stable within a supported release line. Test disabled/default
startup, dependency ordering, config precedence, lifecycle reversal,
capability/doctor merging, collision errors, selector defaults, wheel contents,
and independent installation. An API-breaking contract requires a new core
extension API version; version 1 extensions are trusted and in process.
