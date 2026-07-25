"""Explicit allowlist, dependency ordering, and configuration for extensions."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from importlib import metadata
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..models import ExtensionCapability
from .api import (
    EXTENSION_API_VERSION,
    EmptyExtensionConfig,
    ExtensionContext,
    ExtensionError,
    ExtensionRegistry,
    ExtensionServices,
    call_shutdown,
)

ENTRY_POINT_GROUP = "jlink_mcp.extensions"


class ExtensionManager:
    """Load only allowlisted extensions and own their reverse shutdown order."""

    def __init__(
        self,
        *,
        enabled: Sequence[str],
        config_path: Path | None,
        services: ExtensionServices,
        registry: ExtensionRegistry,
        mcp: Any,
        entry_points: Iterable[Any] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.enabled = _validate_enabled(enabled)
        self.config_path = config_path
        self.services = services
        self.registry = registry
        self.mcp = mcp
        self._entry_points = list(entry_points) if entry_points is not None else None
        self._environ = dict(environ) if environ is not None else dict(os.environ)
        self._loaded: list[Any] = []

    @property
    def loaded_ids(self) -> list[str]:
        return [extension.id for extension in self._loaded]

    def load(self, *, validate: Callable[[], None] | None = None) -> None:
        if not self.enabled:
            return
        available = self._discover()
        objects: dict[str, Any] = {}
        for extension_id in self.enabled:
            matches = available.get(extension_id, [])
            if not matches:
                raise ExtensionError(
                    f"enabled extension is not installed: {extension_id}"
                )
            if len(matches) != 1:
                raise ExtensionError(f"duplicate extension id: {extension_id}")
            try:
                loaded = matches[0].load()
                extension = loaded() if isinstance(loaded, type) else loaded
            except Exception as exc:
                raise ExtensionError(
                    f"could not import extension {extension_id}: {exc}"
                ) from exc
            self._validate_contract(extension_id, extension)
            objects[extension_id] = extension

        order = _topological_order(objects, self.enabled)
        raw_config = _load_config(self.config_path)
        contexts: list[ExtensionContext] = []
        started: list[Any] = []
        info_count = len(self.registry.extension_infos)
        try:
            for extension_id in order:
                extension = objects[extension_id]
                config_data = dict(raw_config.get(extension_id, {}))
                config_data = _apply_environment_overrides(
                    extension_id, config_data, self._environ
                )
                config_model = getattr(extension, "config_model", EmptyExtensionConfig)
                try:
                    config = config_model.model_validate(config_data)
                except (AttributeError, ValidationError, TypeError, ValueError) as exc:
                    raise ExtensionError(
                        f"invalid configuration for extension {extension_id}: {exc}"
                    ) from exc
                context = ExtensionContext(
                    extension_id=extension_id,
                    config=config,
                    services=self.services,
                    registry=self.registry,
                    mcp=self.mcp,
                )
                contexts.append(context)
                started.append(extension)
                try:
                    extension.register(context)
                except Exception as exc:
                    raise ExtensionError(
                        f"initialization failed for extension {extension_id}: {exc}"
                    ) from exc
                self._loaded.append(extension)
                self.registry.extension_infos.append(
                    ExtensionCapability(
                        id=extension.id,
                        version=extension.version,
                        api_version=extension.api_version,
                        dependencies=list(extension.dependencies),
                    )
                )
            if validate is not None:
                try:
                    validate()
                except Exception as exc:
                    raise ExtensionError(
                        f"extension contribution validation failed: {exc}"
                    ) from exc
        except Exception as exc:
            cleanup_errors = self._shutdown_after_failed_load(started)
            for context in reversed(contexts):
                cleanup_errors.extend(context._rollback_registration())
            del self.registry.extension_infos[info_count:]
            self._loaded.clear()
            suffix = (
                "; rollback errors: " + "; ".join(cleanup_errors)
                if cleanup_errors
                else ""
            )
            raise ExtensionError(f"{exc}{suffix}") from exc

    async def shutdown(self) -> None:
        errors: list[str] = []
        for extension in reversed(self._loaded):
            try:
                await call_shutdown(extension.shutdown)
            except Exception as exc:  # noqa: BLE001 - run every shutdown hook
                errors.append(f"{extension.id}: {exc}")
        self._loaded.clear()
        if errors:
            raise ExtensionError("extension shutdown failed: " + "; ".join(errors))

    @staticmethod
    def _shutdown_after_failed_load(extensions: Sequence[Any]) -> list[str]:
        async def run() -> list[str]:
            errors: list[str] = []
            for extension in reversed(extensions):
                try:
                    await call_shutdown(extension.shutdown)
                except Exception as exc:  # noqa: BLE001 - run every rollback hook
                    errors.append(f"{extension.id}: {exc}")
            return errors

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(run())

        result: list[str] = []
        thread_error: list[BaseException] = []

        def execute() -> None:
            try:
                result.extend(asyncio.run(run()))
            except BaseException as exc:  # noqa: BLE001 - cross-thread propagation
                thread_error.append(exc)

        thread = threading.Thread(target=execute, name="jlink-extension-rollback")
        thread.start()
        thread.join()
        if thread_error:
            failure = thread_error[0]
            result.append(
                f"shutdown runner failed: {type(failure).__name__}: {failure}"
            )
        return result

    def _discover(self) -> dict[str, list[Any]]:
        points = self._entry_points
        if points is None:
            discovered = metadata.entry_points()
            points = list(discovered.select(group=ENTRY_POINT_GROUP))
        result: dict[str, list[Any]] = {}
        enabled = set(self.enabled)
        for point in points:
            if point.name in enabled:
                result.setdefault(point.name, []).append(point)
        return result

    @staticmethod
    def _validate_contract(extension_id: str, extension: Any) -> None:
        actual_id = getattr(extension, "id", None)
        if actual_id != extension_id:
            raise ExtensionError(
                f"entry point {extension_id} returned extension id {actual_id!r}"
            )
        if getattr(extension, "api_version", None) != EXTENSION_API_VERSION:
            raise ExtensionError(
                f"unsupported extension API version for {extension_id}: "
                f"{getattr(extension, 'api_version', None)!r}"
            )
        if not isinstance(getattr(extension, "version", None), str):
            raise ExtensionError(f"extension {extension_id} has no string version")
        dependencies = getattr(extension, "dependencies", None)
        if not isinstance(dependencies, Sequence) or isinstance(dependencies, str):
            raise ExtensionError(
                f"extension {extension_id} dependencies must be a sequence"
            )
        if not callable(getattr(extension, "register", None)):
            raise ExtensionError(f"extension {extension_id} has no register method")
        if not callable(getattr(extension, "shutdown", None)):
            raise ExtensionError(f"extension {extension_id} has no shutdown method")


def _validate_enabled(enabled: Sequence[str]) -> list[str]:
    result: list[str] = []
    environment_namespaces: dict[str, str] = {}
    for raw in enabled:
        extension_id = raw.strip()
        if not extension_id:
            continue
        if extension_id in result:
            raise ExtensionError(f"duplicate enabled extension: {extension_id}")
        namespace = _environment_namespace(extension_id)
        if owner := environment_namespaces.get(namespace):
            raise ExtensionError(
                "extension environment namespace collision: "
                f"{owner} and {extension_id} both map to {namespace}"
            )
        environment_namespaces[namespace] = extension_id
        result.append(extension_id)
    return result


def _environment_namespace(extension_id: str) -> str:
    normalized = "".join(
        character.upper() if character.isalnum() else "_" for character in extension_id
    )
    return f"JLINK_MCP_EXT_{normalized}__"


def _topological_order(
    extensions: Mapping[str, Any], enabled_order: Sequence[str]
) -> list[str]:
    enabled = set(extensions)
    for extension_id, extension in extensions.items():
        missing = [item for item in extension.dependencies if item not in enabled]
        if missing:
            raise ExtensionError(
                f"extension {extension_id} has disabled or missing dependencies: "
                + ", ".join(missing)
            )
    order: list[str] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(extension_id: str) -> None:
        if extension_id in visited:
            return
        if extension_id in visiting:
            cycle = visiting[visiting.index(extension_id) :] + [extension_id]
            raise ExtensionError("extension dependency cycle: " + " -> ".join(cycle))
        visiting.append(extension_id)
        for dependency in extensions[extension_id].dependencies:
            visit(dependency)
        visiting.pop()
        visited.add(extension_id)
        order.append(extension_id)

    for extension_id in enabled_order:
        visit(extension_id)
    return order


def _load_config(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    resolved = path.expanduser().resolve(strict=True)
    mode = stat.S_IMODE(resolved.stat().st_mode)
    if mode != 0o600:
        raise ExtensionError(
            f"extension config must have mode 0600, found {mode:04o}: {resolved}"
        )
    with resolved.open("rb") as handle:
        payload = tomllib.load(handle)
    if set(payload) - {"extensions"}:
        raise ExtensionError("extension config may only contain [extensions.*] tables")
    sections = payload.get("extensions", {})
    if not isinstance(sections, dict):
        raise ExtensionError("extension config [extensions] value must be a table")
    result: dict[str, dict[str, Any]] = {}
    for extension_id, values in sections.items():
        if not isinstance(values, dict):
            raise ExtensionError(
                f"extension config section must be a table: {extension_id}"
            )
        result[str(extension_id)] = dict(values)
    return result


def _apply_environment_overrides(
    extension_id: str,
    config: dict[str, Any],
    environ: Mapping[str, str],
) -> dict[str, Any]:
    prefix = _environment_namespace(extension_id)
    result = dict(config)
    for name, raw in environ.items():
        if not name.startswith(prefix):
            continue
        path = [item.lower() for item in name[len(prefix) :].split("__") if item]
        if not path:
            raise ExtensionError(f"invalid extension environment override: {name}")
        target = result
        for part in path[:-1]:
            child = target.setdefault(part, {})
            if not isinstance(child, dict):
                raise ExtensionError(
                    f"extension environment override conflicts with field: {name}"
                )
            target = child
        target[path[-1]] = _parse_environment_value(raw)
    return result


def _parse_environment_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
