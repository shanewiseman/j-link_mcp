"""Extension-owned Arduino toolchain and fixture configuration."""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _first_existing(*paths: str) -> Path:
    for raw in paths:
        path = Path(raw)
        if path.exists():
            return path.resolve()
    return Path(paths[0])


class ArduinoGigaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arduino_cli: str = Field(
        default_factory=lambda: shutil.which("arduino-cli")
        or "/usr/local/bin/arduino-cli"
    )
    data_root: Path = Field(
        default_factory=lambda: _first_existing(
            "/opt/arduino/data", str(Path.home() / ".arduino15")
        )
    )
    user_root: Path = Field(
        default_factory=lambda: _first_existing(
            "/opt/arduino/user", str(Path.home() / "Arduino")
        )
    )
    fqbn: str = "arduino:mbed_giga:giga"
    flash_split: str = "75_25"
    test_target_disposable: bool = False

    @field_validator("data_root", "user_root", mode="before")
    @classmethod
    def expand_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser()

    @field_validator("flash_split")
    @classmethod
    def validate_flash_split(cls, value: str) -> str:
        if value not in {"100_0", "75_25", "50_50"}:
            raise ValueError("flash_split must be 100_0, 75_25, or 50_50")
        return value
