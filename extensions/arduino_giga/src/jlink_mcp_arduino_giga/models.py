"""Public Arduino build contracts owned by this extension."""

from __future__ import annotations

from pydantic import BaseModel, Field

from jlink_mcp.models import (
    Artifact,
    CommandResult,
    DeviceSelector as CoreDeviceSelector,
    ValidationReport as CoreValidationReport,
)

from .profiles import TargetCore


class DeviceSelector(CoreDeviceSelector):
    """Stable identity used for every target-changing operation."""

    target_profile: str = "arduino_giga_r1"
    core: TargetCore = TargetCore.M7


class BuildResult(BaseModel):
    core: TargetCore
    fqbn: str
    build_directory: str
    command: CommandResult
    artifacts: list[Artifact]
    properties: dict[str, str] = Field(default_factory=dict)


class ValidationReport(CoreValidationReport):
    selector: DeviceSelector
