"""Runtime configuration and path confinement."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    repository_root: Path = Field(default_factory=lambda: Path.cwd().resolve())
    workspace_root: Path = Field(default_factory=lambda: Path.cwd().resolve())
    state_root: Path = Field(default_factory=lambda: (Path.cwd() / "state").resolve())
    segger_root: Path = Field(
        default_factory=lambda: _first_existing(
            "/opt/segger/JLink",
            "/opt/SEGGER/JLink",
            "/home/swiseman/Documents/Arduino/JLink",
        )
    )
    ozone_root: Path | None = None
    systemview_root: Path | None = None
    host_dev_root: Path = Path("/dev")
    sys_usb_root: Path = Path("/sys/bus/usb/devices")

    arduino_cli: str = Field(
        default_factory=lambda: shutil.which("arduino-cli")
        or "/home/swiseman/.local/bin/arduino-cli"
    )
    arduino_data_root: Path = Field(
        default_factory=lambda: _first_existing(
            "/opt/arduino/data",
            str(Path.home() / ".arduino15"),
        )
    )
    arm_gdb: str = Field(
        default_factory=lambda: shutil.which("arm-none-eabi-gdb")
        or "/home/swiseman/.arduino15/packages/arduino/tools/"
        "arm-none-eabi-gcc/7-2017q4/bin/arm-none-eabi-gdb"
    )
    fqbn: str = "arduino:mbed_giga:giga"
    flash_split: str = "75_25"
    default_timeout_seconds: float = Field(default=30.0, gt=0, le=3600)
    max_output_bytes: int = Field(default=4_000_000, ge=1024)
    test_target_disposable: bool = False
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
        "arduino_data_root",
        mode="before",
    )
    @classmethod
    def expand_path(cls, value: str | Path) -> Path:
        if value is None:
            return value
        return Path(value).expanduser()

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
