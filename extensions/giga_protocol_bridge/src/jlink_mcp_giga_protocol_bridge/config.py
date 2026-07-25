"""Extension-owned protocol bridge configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator


class GigaProtocolBridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles_file: Path | None = None

    @field_validator("profiles_file", mode="before")
    @classmethod
    def expand_path(cls, value: str | Path | None) -> Path | None:
        if value is None or value == "":
            return None
        return Path(value).expanduser()
