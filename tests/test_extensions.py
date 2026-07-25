from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from jlink_mcp.extensions.api import (
    EXTENSION_API_VERSION,
    ArtifactService,
    CapabilityContribution,
    ExtensionError,
    ExtensionRegistry,
    ExtensionServices,
)
from jlink_mcp.extensions.loader import ExtensionManager
from jlink_mcp.models import (
    BoardCapabilities,
    CapabilityAvailability,
    CapabilityManifest,
    CapabilityState,
    DependencyCheck,
    ToolAvailability,
    USBDevice,
)
from jlink_mcp.profiles import CoreProfile, TargetProfile


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}
        self.resources: dict[str, Any] = {}

    def tool(self, *, name: str, annotations: Any = None):
        def decorate(function):
            self.tools[name] = function
            return function

        return decorate

    def resource(self, uri: str, *, name: str | None, mime_type: str | None):
        def decorate(function):
            self.resources[uri] = function
            return function

        return decorate


@dataclass
class FakeEntryPoint:
    name: str
    value: Any

    def load(self) -> Any:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "default"
    count: int = 1


class FakeExtension:
    version = "1.2.3"
    api_version = EXTENSION_API_VERSION
    dependencies: tuple[str, ...] = ()
    config_model = Config

    def __init__(
        self,
        extension_id: str,
        events: list[str],
        *,
        dependencies: tuple[str, ...] = (),
        register=None,
    ) -> None:
        self.id = extension_id
        self.dependencies = dependencies
        self.events = events
        self._register = register

    def register(self, context) -> None:
        self.events.append(f"register:{self.id}:{context.config.label}")
        if self._register:
            self._register(context)

    async def shutdown(self) -> None:
        self.events.append(f"shutdown:{self.id}")


def services() -> ExtensionServices:
    return ExtensionServices(
        jlink=object(),
        serial=object(),
        artifacts=ArtifactService(None, None),
        audit=object(),
        paths=object(),
        process=object(),
    )


def manager(
    extensions: list[FakeExtension],
    *,
    enabled: list[str] | None = None,
    config_path: Path | None = None,
    registry: ExtensionRegistry | None = None,
    mcp: FakeMCP | None = None,
    environ: dict[str, str] | None = None,
    points: list[FakeEntryPoint] | None = None,
) -> ExtensionManager:
    return ExtensionManager(
        enabled=enabled or [item.id for item in extensions],
        config_path=config_path,
        services=services(),
        registry=registry or ExtensionRegistry(),
        mcp=mcp or FakeMCP(),
        entry_points=points
        if points is not None
        else [FakeEntryPoint(item.id, item) for item in extensions],
        environ=environ or {},
    )


@pytest.mark.asyncio
async def test_explicit_enablement_dependency_order_and_reverse_shutdown() -> None:
    events: list[str] = []
    base = FakeExtension("base", events)
    child = FakeExtension("child", events, dependencies=("base",))
    loaded = manager([child, base], enabled=["child", "base"])

    loaded.load()

    assert loaded.loaded_ids == ["base", "child"]
    assert events == ["register:base:default", "register:child:default"]
    await loaded.shutdown()
    assert events[-2:] == ["shutdown:child", "shutdown:base"]


def test_toml_configuration_and_environment_precedence(tmp_path: Path) -> None:
    path = tmp_path / "extensions.toml"
    path.write_text(
        "[extensions.sample]\nlabel = 'from-file'\ncount = 2\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    events: list[str] = []
    extension = FakeExtension("sample", events)
    loaded = manager(
        [extension],
        config_path=path,
        environ={
            "JLINK_MCP_EXT_SAMPLE__LABEL": "from-environment",
            "JLINK_MCP_EXT_SAMPLE__COUNT": "3",
        },
    )

    loaded.load()

    assert events == ["register:sample:from-environment"]


def test_enabled_extension_environment_namespaces_must_be_unique() -> None:
    events: list[str] = []
    with pytest.raises(ExtensionError, match="environment namespace collision"):
        manager(
            [
                FakeExtension("foo-bar", events),
                FakeExtension("foo_bar", events),
            ]
        )
    assert events == []


def test_profile_detector_tool_resource_capability_and_doctor_registration() -> None:
    events: list[str] = []
    registry = ExtensionRegistry()
    mcp = FakeMCP()

    def register(context) -> None:
        profile = TargetProfile(
            id="sample_target",
            display_name="Sample target",
            cores={
                "primary": CoreProfile(
                    id="primary",
                    jlink_device="SAMPLE_DEVICE",
                    expected_core="Sample-Core",
                    expected_cpuid=0x12345678,
                )
            },
            default_core="primary",
            expected_dpidr=0x12345678,
        )
        context.register_target_profile(profile)
        context.register_board_detector(
            "sample",
            lambda usb: (
                BoardCapabilities(
                    serial=usb.serial,
                    model="Sample",
                    target_profile="sample_target",
                    mcu="sample",
                    cores=["primary"],
                    usb=usb,
                )
                if usb.vendor_id == "1234"
                else None
            ),
        )

        @context.register_tool(name="sample_tool")
        def tool() -> str:
            return "ok"

        @context.register_resource("sample://status", name="status")
        def resource() -> str:
            return "ok"

        context.register_capability_provider(
            lambda manifest: CapabilityContribution(
                tools=[
                    ToolAvailability(name="sample-cli", state=CapabilityState.AVAILABLE)
                ],
                workflows={"sample_workflow": CapabilityState.AVAILABLE},
                workflow_details={
                    "sample_workflow": CapabilityAvailability(
                        state=CapabilityState.AVAILABLE
                    )
                },
                features={
                    "sample_feature": CapabilityAvailability(
                        state=CapabilityState.UNKNOWN
                    )
                },
                limitations=["sample limitation"],
                atomic_tools=["sample_tool"],
            )
        )
        context.register_dependency_provider(
            lambda manifest: [DependencyCheck(name="sample-check", ok=True)]
        )

    loaded = manager(
        [FakeExtension("sample", events, register=register)],
        registry=registry,
        mcp=mcp,
    )
    loaded.load()
    manifest = registry.merge_capabilities(
        CapabilityManifest(host_os="test", host_arch="test")
    )
    usb = USBDevice(kind="usb", vendor_id="1234", product_id="5678", serial="stable")

    assert registry.targets.detect_board(usb).target_profile == "sample_target"
    assert mcp.tools["sample_tool"]() == "ok"
    assert mcp.resources["sample://status"]() == "ok"
    assert manifest.extensions[0].id == "sample"
    assert manifest.workflows["sample_workflow"] == CapabilityState.AVAILABLE
    assert registry.dependency_checks(manifest)[0].name == "sample-check"

    with pytest.raises(ExtensionError, match="sample-check"):
        registry.dependency_checks(
            manifest,
            existing_checks=[DependencyCheck(name="sample-check", ok=True)],
        )


@pytest.mark.parametrize(
    ("points", "enabled", "message"),
    [
        ([], ["missing"], "not installed"),
        (
            [
                FakeEntryPoint("duplicate", FakeExtension("duplicate", [])),
                FakeEntryPoint("duplicate", FakeExtension("duplicate", [])),
            ],
            ["duplicate"],
            "duplicate extension id",
        ),
        (
            [FakeEntryPoint("broken", RuntimeError("import failed"))],
            ["broken"],
            "could not import",
        ),
    ],
)
def test_entry_point_failures(points, enabled, message) -> None:
    loaded = manager([], points=points, enabled=enabled)
    with pytest.raises(ExtensionError, match=message):
        loaded.load()


def test_disabled_dependency_and_cycle_failures() -> None:
    events: list[str] = []
    dependent = FakeExtension("dependent", events, dependencies=("base",))
    with pytest.raises(ExtensionError, match="disabled or missing"):
        manager([dependent]).load()

    first = FakeExtension("first", events, dependencies=("second",))
    second = FakeExtension("second", events, dependencies=("first",))
    with pytest.raises(ExtensionError, match="dependency cycle"):
        manager([first, second]).load()


def test_incompatible_api_invalid_contract_config_and_initialization() -> None:
    events: list[str] = []
    incompatible = FakeExtension("incompatible", events)
    incompatible.api_version = 2
    with pytest.raises(ExtensionError, match="unsupported extension API"):
        manager([incompatible]).load()

    invalid_config = FakeExtension("invalid", events)
    with pytest.raises(ExtensionError, match="invalid configuration"):
        manager(
            [invalid_config],
            environ={"JLINK_MCP_EXT_INVALID__UNKNOWN": "value"},
        ).load()

    failing = FakeExtension(
        "failing",
        events,
        register=lambda context: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(ExtensionError, match="initialization failed"):
        manager([failing]).load()


def test_failed_load_shuts_down_and_rolls_back_all_registrations() -> None:
    events: list[str] = []
    registry = ExtensionRegistry()
    mcp = FakeMCP()

    def profile(profile_id: str) -> TargetProfile:
        return TargetProfile(
            id=profile_id,
            display_name=profile_id,
            cores={
                "core": CoreProfile(
                    id="core",
                    jlink_device=profile_id.upper(),
                    expected_core="Test-Core",
                    expected_cpuid=1,
                )
            },
            default_core="core",
            expected_dpidr=1,
        )

    def register_base(context) -> None:
        context.register_target_profile(profile("base_profile"))
        context.register_board_detector("board", lambda usb: None)
        context.register_capability_provider(lambda manifest: CapabilityContribution())
        context.register_dependency_provider(lambda manifest: [])
        context.publish_service("service", object())
        context.register_tool(lambda: None, name="base_tool")
        context.register_resource("base://resource", lambda: None, name="base")

    def register_failing(context) -> None:
        context.register_target_profile(profile("partial_profile"))
        context.register_board_detector("board", lambda usb: None)
        context.register_capability_provider(lambda manifest: CapabilityContribution())
        context.register_dependency_provider(lambda manifest: [])
        context.publish_service("service", object())
        context.register_tool(lambda: None, name="partial_tool")
        context.register_resource("partial://resource", lambda: None, name="partial")
        raise RuntimeError("partial registration failed")

    base = FakeExtension("base", events, register=register_base)
    failing = FakeExtension(
        "failing",
        events,
        dependencies=("base",),
        register=register_failing,
    )
    loaded = manager(
        [base, failing],
        enabled=["base", "failing"],
        registry=registry,
        mcp=mcp,
    )

    with pytest.raises(ExtensionError, match="partial registration failed"):
        loaded.load()

    assert events == [
        "register:base:default",
        "register:failing:default",
        "shutdown:failing",
        "shutdown:base",
    ]
    assert loaded.loaded_ids == []
    assert registry.extension_infos == []
    assert dict(registry.targets.profiles) == {}
    assert dict(registry.targets.detectors) == {}
    assert registry._capability_providers == []
    assert registry._dependency_providers == []
    assert registry._services == {}
    assert mcp.tools == {}
    assert mcp.resources == {}


@pytest.mark.parametrize("collision", ["capability", "dependency"])
def test_contribution_validation_collisions_roll_back(collision: str) -> None:
    events: list[str] = []
    registry = ExtensionRegistry()

    def register(context) -> None:
        context.register_capability_provider(
            lambda manifest: CapabilityContribution(
                tools=(
                    [
                        ToolAvailability(
                            name="core-capability",
                            state=CapabilityState.AVAILABLE,
                        )
                    ]
                    if collision == "capability"
                    else []
                )
            )
        )
        context.register_dependency_provider(
            lambda manifest: (
                [DependencyCheck(name="core-dependency", ok=True)]
                if collision == "dependency"
                else []
            )
        )

    loaded = manager(
        [FakeExtension("colliding", events, register=register)],
        registry=registry,
    )

    def validate() -> None:
        manifest = registry.merge_capabilities(
            CapabilityManifest(
                host_os="test",
                host_arch="test",
                tools=[
                    ToolAvailability(
                        name="core-capability",
                        state=CapabilityState.AVAILABLE,
                    )
                ],
            )
        )
        registry.dependency_checks(
            manifest,
            existing_checks=[DependencyCheck(name="core-dependency", ok=True)],
        )

    with pytest.raises(ExtensionError, match="duplicate"):
        loaded.load(validate=validate)

    assert events == ["register:colliding:default", "shutdown:colliding"]
    assert loaded.loaded_ids == []
    assert registry.extension_infos == []
    assert registry._capability_providers == []
    assert registry._dependency_providers == []


@pytest.mark.asyncio
async def test_failed_load_rolls_back_inside_an_active_event_loop() -> None:
    events: list[str] = []
    registry = ExtensionRegistry()
    mcp = FakeMCP()

    def register_failing(context) -> None:
        context.register_tool(lambda: None, name="partial_tool")
        context.register_resource("partial://resource", lambda: None, name="partial")
        raise RuntimeError("active-loop registration failed")

    failing = FakeExtension("failing", events, register=register_failing)
    loaded = manager([failing], registry=registry, mcp=mcp)

    with pytest.raises(ExtensionError, match="active-loop registration failed"):
        loaded.load()

    assert events == ["register:failing:default", "shutdown:failing"]
    assert loaded.loaded_ids == []
    assert registry.extension_infos == []
    assert mcp.tools == {}
    assert mcp.resources == {}


@pytest.mark.asyncio
async def test_shutdown_runs_all_hooks_and_reports_errors() -> None:
    events: list[str] = []

    class FailingShutdown(FakeExtension):
        async def shutdown(self) -> None:
            self.events.append(f"shutdown:{self.id}")
            raise RuntimeError("shutdown failed")

    failing = FailingShutdown("failing", events)
    base = FakeExtension("base", events)
    loaded = manager([failing, base])
    loaded.load()

    with pytest.raises(ExtensionError, match="failing: shutdown failed"):
        await loaded.shutdown()

    assert events[-2:] == ["shutdown:base", "shutdown:failing"]
    assert loaded.loaded_ids == []


def test_unsafe_config_permissions_fail(tmp_path: Path) -> None:
    path = tmp_path / "extensions.toml"
    path.write_text("[extensions.sample]\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(ExtensionError, match="mode 0600"):
        manager([FakeExtension("sample", [])], config_path=path).load()


@pytest.mark.parametrize("collision", ["tool", "resource", "profile"])
def test_registration_collisions_fail(collision: str) -> None:
    registry = ExtensionRegistry(
        tool_names={"taken_tool"}, resource_uris={"taken://resource"}
    )
    registry.targets.register_profile(
        TargetProfile(
            id="taken_profile",
            display_name="Taken",
            cores={
                "core": CoreProfile(
                    id="core",
                    jlink_device="TAKEN",
                    expected_core="Taken-Core",
                    expected_cpuid=1,
                )
            },
            default_core="core",
            expected_dpidr=1,
        )
    )

    def register(context) -> None:
        if collision == "tool":
            context.register_tool(lambda: None, name="taken_tool")
        elif collision == "resource":
            context.register_resource("taken://resource", lambda: None, name="taken")
        else:
            context.register_target_profile(
                registry.targets.get_profile("taken_profile")
            )

    with pytest.raises(ExtensionError, match="initialization failed"):
        manager(
            [FakeExtension("sample", [], register=register)], registry=registry
        ).load()
