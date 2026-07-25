"""Runtime configuration and path confinement."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _first_existing(*paths: str) -> Path:
    for raw in paths:
        path = Path(raw)
        if path.exists():
            return path.resolve()
    return Path(paths[0])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JLINK_MCP_",
        env_file=".env",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    mcp_path: str = "/mcp"
    token: str | None = None
    token_file: Path | None = None
    extensions: Annotated[list[str], NoDecode] = Field(default_factory=list)
    extension_config: Path | None = None

    repository_root: Path = Field(default_factory=lambda: Path.cwd().resolve())
    workspace_root: Path = Field(default_factory=lambda: Path.cwd().resolve())
    state_root: Path = Field(default_factory=lambda: (Path.cwd() / "state").resolve())
    segger_root: Path = Field(
        default_factory=lambda: _first_existing(
            "/opt/segger/JLink",
            "/opt/SEGGER/JLink",
        )
    )
    ozone_root: Path | None = None
    systemview_root: Path | None = None
    host_dev_root: Path = Path("/dev")
    sys_usb_root: Path = Path("/sys/bus/usb/devices")

    gdb_client: str = Field(
        default_factory=lambda: shutil.which("gdb-multiarch")
        or shutil.which("gdb")
        or shutil.which("arm-none-eabi-gdb")
        or "gdb-multiarch"
    )
    default_timeout_seconds: float = Field(default=30.0, gt=0, le=3600)
    max_output_bytes: int = Field(default=4_000_000, ge=1024)
    enable_gui: bool = True
    display: str = ":99"

    @field_validator(
        "repository_root",
        "workspace_root",
        "state_root",
        "segger_root",
        "ozone_root",
        "systemview_root",
        "host_dev_root",
        "sys_usb_root",
        "extension_config",
        mode="before",
    )
    @classmethod
    def expand_path(cls, value: str | Path | None) -> Path | None:
        if value is None or value == "":
            return value
        return Path(value).expanduser()

    @field_validator("extensions", mode="before")
    @classmethod
    def parse_extensions(cls, value: object) -> object:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    def ensure_directories(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        (self.state_root / "commands").mkdir(parents=True, exist_ok=True)
        (self.state_root / "artifacts").mkdir(parents=True, exist_ok=True)
        (self.state_root / "screenshots").mkdir(parents=True, exist_ok=True)

    def bearer_token(self, *, required: bool = True) -> str | None:
        if self.token:
            return self.token.strip()
        if self.token_file and self.token_file.exists():
            return self.token_file.read_text(encoding="utf-8").strip()
        from_env = os.environ.get("JLINK_MCP_TOKEN")
        if from_env:
            return from_env.strip()
        if required:
            raise RuntimeError(
                "A bearer token is required. Set JLINK_MCP_TOKEN or "
                "JLINK_MCP_TOKEN_FILE."
            )
        return None

    def resolve_workspace_path(
        self, raw_path: str | Path, *, must_exist: bool = True
    ) -> Path:
        path = Path(raw_path)
        if not path.is_absolute():
            path = self.workspace_root / path
        if must_exist:
            resolved = path.resolve(strict=True)
        else:
            resolved = path.resolve(strict=False)
        workspace = self.workspace_root.resolve(strict=True)
        if resolved != workspace and workspace not in resolved.parents:
            raise ValueError(f"path is outside workspace root: {raw_path}")
        return resolved

    def resolve_allowed_path(
        self, raw_path: str | Path, *, must_exist: bool = True
    ) -> Path:
        """Resolve a path confined to the workspace or persistent state."""

        path = Path(raw_path)
        if not path.is_absolute():
            path = self.workspace_root / path
        resolved = path.resolve(strict=must_exist)
        roots = (
            self.workspace_root.resolve(strict=True),
            self.state_root.resolve(strict=True),
        )
        if not any(resolved == root or root in resolved.parents for root in roots):
            raise ValueError(f"path is outside configured roots: {raw_path}")
        return resolved

    def segger_executable(self, name: str) -> Path:
        path = (self.segger_root / name).resolve(strict=False)
        root = self.segger_root.resolve(strict=True)
        if root not in path.parents or not path.is_file():
            raise FileNotFoundError(f"SEGGER executable not found: {name}")
        if not os.access(path, os.X_OK):
            raise PermissionError(f"SEGGER executable is not executable: {path}")
        return path
