"""Versioned contracts available to trusted in-process extensions."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from ..artifacts import inspect_elf, registerable_artifact
from ..models import (
    Artifact,
    CapabilityAvailability,
    CapabilityManifest,
    CapabilityState,
    DependencyCheck,
    ExtensionCapability,
    ToolAvailability,
)
from ..profiles import BoardDetector, TargetProfile, TargetRegistry

EXTENSION_API_VERSION = 1


class ExtensionError(RuntimeError):
    """Raised when an enabled extension violates the public contract."""


class EmptyExtensionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityContribution(BaseModel):
    """Namespaced capability data merged into the core manifest."""

    tools: list[ToolAvailability] = Field(default_factory=list)
    workflows: dict[str, CapabilityState] = Field(default_factory=dict)
    workflow_details: dict[str, CapabilityAvailability] = Field(default_factory=dict)
    features: dict[str, CapabilityAvailability] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    raw_surfaces: list[str] = Field(default_factory=list)
    atomic_tools: list[str] = Field(default_factory=list)


CapabilityProvider = Callable[[CapabilityManifest], CapabilityContribution]
DependencyProvider = Callable[[CapabilityManifest], Sequence[DependencyCheck]]
ShutdownHook = Callable[[], None | Awaitable[None]]


@runtime_checkable
class Extension(Protocol):
    """Entry-point object contract for extension API version 1."""

    id: str
    version: str
    api_version: int
    dependencies: Sequence[str]
    config_model: type[BaseModel]

    def register(self, context: ExtensionContext) -> None: ...

    def shutdown(self) -> None | Awaitable[None]: ...


@dataclass(frozen=True, slots=True)
class ArtifactService:
    """Hash, inspect, and persist artifacts through the core audit store."""

    settings: Any
    store: Any

    def register(self, path: str | Path, *, kind: str) -> Artifact:
        resolved = self.settings.resolve_allowed_path(path)
        artifact = registerable_artifact(resolved, kind=kind)
        self.store.register_artifact(artifact)
        return artifact

    def inspect_elf(self, path: str | Path) -> dict[str, Any]:
        return inspect_elf(self.settings.resolve_allowed_path(path))


@dataclass(frozen=True, slots=True)
class ExtensionServices:
    """Stable core services exposed to extensions without runtime internals."""

    jlink: Any
    serial: Any
    artifacts: ArtifactService
    audit: Any
    paths: Any
    process: Any


@dataclass(slots=True)
class ExtensionRegistry:
    """Mutable registrations owned by one MCP runtime."""

    targets: TargetRegistry = field(default_factory=TargetRegistry)
    extension_infos: list[ExtensionCapability] = field(default_factory=list)
    tool_names: set[str] = field(default_factory=set)
    resource_uris: set[str] = field(default_factory=set)
    _capability_providers: list[tuple[str, CapabilityProvider]] = field(
        default_factory=list
    )
    _dependency_providers: list[tuple[str, DependencyProvider]] = field(
        default_factory=list
    )
    _services: dict[tuple[str, str], Any] = field(default_factory=dict)

    def merge_capabilities(self, manifest: CapabilityManifest) -> CapabilityManifest:
        tool_names = {tool.name for tool in manifest.tools}
        workflow_names = set(manifest.workflows)
        detail_names = set(manifest.workflow_details)
        feature_names = set(manifest.features)
        for extension_id, provider in self._capability_providers:
            try:
                contribution = CapabilityContribution.model_validate(provider(manifest))
            except Exception as exc:
                raise ExtensionError(
                    f"capability contribution failed for {extension_id}: {exc}"
                ) from exc
            for tool in contribution.tools:
                if tool.name in tool_names:
                    raise ExtensionError(f"duplicate capability tool: {tool.name}")
                tool_names.add(tool.name)
                manifest.tools.append(tool)
            _merge_unique(
                manifest.workflows,
                contribution.workflows,
                workflow_names,
                "workflow",
            )
            _merge_unique(
                manifest.workflow_details,
                contribution.workflow_details,
                detail_names,
                "workflow detail",
            )
            _merge_unique(
                manifest.features,
                contribution.features,
                feature_names,
                "feature",
            )
            manifest.limitations.extend(contribution.limitations)
            manifest.raw_surfaces.extend(contribution.raw_surfaces)
            manifest.atomic_tools.extend(contribution.atomic_tools)
        manifest.extensions = list(self.extension_infos)
        return manifest

    def dependency_checks(self, manifest: CapabilityManifest) -> list[DependencyCheck]:
        checks: list[DependencyCheck] = []
        names: set[str] = set()
        for extension_id, provider in self._dependency_providers:
            try:
                contributed = provider(manifest)
            except Exception as exc:
                raise ExtensionError(
                    f"dependency contribution failed for {extension_id}: {exc}"
                ) from exc
            for raw in contributed:
                check = DependencyCheck.model_validate(raw)
                if check.name in names:
                    raise ExtensionError(
                        f"duplicate extension dependency check: {check.name}"
                    )
                names.add(check.name)
                checks.append(check)
        return checks


def _merge_unique(
    target: dict[str, Any],
    contribution: Mapping[str, Any],
    names: set[str],
    kind: str,
) -> None:
    for name, value in contribution.items():
        if name in names:
            raise ExtensionError(f"duplicate capability {kind}: {name}")
        names.add(name)
        target[name] = value


class ExtensionContext:
    """Only supported registration and service surface for extensions."""

    def __init__(
        self,
        *,
        extension_id: str,
        config: BaseModel,
        services: ExtensionServices,
        registry: ExtensionRegistry,
        mcp: Any,
    ) -> None:
        self.extension_id = extension_id
        self.config = config
        self.services = services
        self._registry = registry
        self._mcp = mcp
        self._rollback_actions: list[Callable[[], None]] = []

    def _rollback_registration(self) -> list[str]:
        """Undo this context's registrations in reverse order."""

        errors: list[str] = []
        for action in reversed(self._rollback_actions):
            try:
                action()
            except Exception as exc:  # noqa: BLE001 - continue all inverse actions
                errors.append(f"{type(exc).__name__}: {exc}")
        self._rollback_actions.clear()
        return errors

    def register_target_profile(self, profile: TargetProfile) -> None:
        self._registry.targets.register_profile(profile)
        self._rollback_actions.append(
            lambda: self._registry.targets.unregister_profile(profile.id)
        )

    def register_board_detector(
        self, detector_id: str, detector: BoardDetector
    ) -> None:
        namespaced = f"{self.extension_id}:{detector_id}"
        self._registry.targets.register_board_detector(namespaced, detector)
        self._rollback_actions.append(
            lambda: self._registry.targets.unregister_board_detector(namespaced)
        )

    def register_capability_provider(self, provider: CapabilityProvider) -> None:
        entry = (self.extension_id, provider)
        self._registry._capability_providers.append(entry)
        self._rollback_actions.append(
            lambda: self._registry._capability_providers.remove(entry)
        )

    def register_dependency_provider(self, provider: DependencyProvider) -> None:
        entry = (self.extension_id, provider)
        self._registry._dependency_providers.append(entry)
        self._rollback_actions.append(
            lambda: self._registry._dependency_providers.remove(entry)
        )

    def publish_service(self, name: str, service: Any) -> None:
        key = (self.extension_id, name)
        if key in self._registry._services:
            raise ExtensionError(
                f"duplicate extension service: {self.extension_id}:{name}"
            )
        self._registry._services[key] = service
        self._rollback_actions.append(lambda: self._registry._services.pop(key, None))

    def require_extension_service(self, extension_id: str, name: str) -> Any:
        try:
            return self._registry._services[(extension_id, name)]
        except KeyError as exc:
            raise ExtensionError(
                f"required extension service is unavailable: {extension_id}:{name}"
            ) from exc

    def _remove_tool(self, tool_name: str) -> None:
        self._registry.tool_names.discard(tool_name)
        remove_tool = getattr(self._mcp, "remove_tool", None)
        if callable(remove_tool):
            remove_tool(tool_name)
            return
        tools = getattr(self._mcp, "tools", None)
        if isinstance(tools, dict):
            tools.pop(tool_name, None)

    def _remove_resource(self, uri: str) -> None:
        self._registry.resource_uris.discard(uri)
        resources = getattr(self._mcp, "resources", None)
        if isinstance(resources, dict):
            resources.pop(uri, None)
            return
        manager = getattr(self._mcp, "_resource_manager", None)
        if manager is None:
            return
        for collection_name in ("_resources", "_templates"):
            collection = getattr(manager, collection_name, None)
            if not isinstance(collection, dict):
                continue
            for key in list(collection):
                if str(key) == uri:
                    collection.pop(key, None)

    def register_tool(
        self,
        function: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        annotations: ToolAnnotations | None = None,
    ) -> Callable[..., Any]:
        def install(candidate: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or candidate.__name__
            if tool_name in self._registry.tool_names:
                raise ExtensionError(f"duplicate MCP tool: {tool_name}")
            self._registry.tool_names.add(tool_name)
            try:
                self._mcp.tool(name=tool_name, annotations=annotations)(candidate)
            except Exception:
                self._remove_tool(tool_name)
                raise
            self._rollback_actions.append(lambda: self._remove_tool(tool_name))
            return candidate

        return install(function) if function is not None else install

    def register_resource(
        self,
        uri: str,
        function: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        mime_type: str | None = None,
    ) -> Callable[..., Any]:
        def install(candidate: Callable[..., Any]) -> Callable[..., Any]:
            if uri in self._registry.resource_uris:
                raise ExtensionError(f"duplicate MCP resource: {uri}")
            self._registry.resource_uris.add(uri)
            try:
                self._mcp.resource(uri, name=name, mime_type=mime_type)(candidate)
            except Exception:
                self._remove_resource(uri)
                raise
            self._rollback_actions.append(lambda: self._remove_resource(uri))
            return candidate

        return install(function) if function is not None else install


async def call_shutdown(hook: ShutdownHook) -> None:
    result = hook()
    if inspect.isawaitable(result):
        await result
