"""Typed public contracts shared by MCP tools and backend adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class TargetCore(StrEnum):
    M7 = "m7"
    M4 = "m4"


class TargetState(StrEnum):
    UNKNOWN = "unknown"
    DISCONNECTED = "disconnected"
    RUNNING = "running"
    HALTED = "halted"
    RESET = "reset"
    FAULTED = "faulted"


class CapabilityState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class DeviceSelector(BaseModel):
    """Stable identity used for every target-changing operation."""

    model_config = ConfigDict(extra="forbid")

    probe_serial: str | None = Field(
        default=None,
        description="J-Link serial. Omit only when discovery has one unique probe.",
    )
    board_serial: str | None = Field(
        default=None,
        description="USB board serial used to correlate the target board.",
    )
    target_profile: str = "arduino_giga_r1"
    core: TargetCore = TargetCore.M7
    interface: str = "SWD"
    speed_khz: int = Field(default=4000, ge=5, le=50000)

    @field_validator("probe_serial", "board_serial")
    @classmethod
    def validate_serial(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value or not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("serial identifiers must be non-empty alphanumerics")
        return value


class USBDevice(BaseModel):
    kind: str
    vendor_id: str
    product_id: str
    manufacturer: str | None = None
    product: str | None = None
    serial: str | None = None
    bus: str | None = None
    address: str | None = None
    sys_path: str | None = None
    device_nodes: list[str] = Field(default_factory=list)


class ToolAvailability(BaseModel):
    name: str
    state: CapabilityState
    path: str | None = None
    version: str | None = None
    reason: str | None = None


class CapabilityAvailability(BaseModel):
    state: CapabilityState
    reason: str | None = None
    dependencies: list[str] = Field(default_factory=list)


class ProbeCapabilities(BaseModel):
    serial: str
    model: str
    firmware: str | None = None
    usb: USBDevice
    licenses: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=lambda: ["SWD"])
    max_swd_speed_khz: int | None = None
    max_swo_speed_khz: int | None = None
    target_power: bool | None = None
    trace: list[str] = Field(default_factory=list)


class BoardCapabilities(BaseModel):
    serial: str | None
    model: str
    fqbn: str | None = None
    mcu: str
    cores: list[TargetCore]
    usb: USBDevice | None = None
    serial_port: str | None = None


class CapabilityManifest(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    host_os: str
    host_arch: str
    probes: list[ProbeCapabilities] = Field(default_factory=list)
    boards: list[BoardCapabilities] = Field(default_factory=list)
    tools: list[ToolAvailability] = Field(default_factory=list)
    workflows: dict[str, CapabilityState] = Field(default_factory=dict)
    workflow_details: dict[str, CapabilityAvailability] = Field(default_factory=dict)
    features: dict[str, CapabilityAvailability] = Field(default_factory=dict)
    raw_surfaces: list[str] = Field(default_factory=list)
    atomic_tools: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    unique_pair: bool = False
    selected_probe_serial: str | None = None
    selected_board_serial: str | None = None


class CommandResult(BaseModel):
    operation_id: str
    session_id: str | None = None
    backend: str
    command: list[str]
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    return_code: int | None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    parsed: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    target_state_before: TargetState = TargetState.UNKNOWN
    target_state_after: TargetState = TargetState.UNKNOWN
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    evidence_paths: list[str] = Field(default_factory=list)
    probe_identity: dict[str, Any] = Field(default_factory=dict)
    target_identity: dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def ok(self) -> bool:
        return self.return_code == 0 and not self.timed_out


class DependencyCheck(BaseModel):
    name: str
    ok: bool
    required: bool = True
    observed: str | None = None
    expected: str | None = None
    remediation: str | None = None


class DependencyReport(BaseModel):
    generated_at: datetime = Field(default_factory=utc_now)
    checks: list[DependencyCheck]
    manifest: CapabilityManifest

    @computed_field
    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if check.required)


class Artifact(BaseModel):
    path: str
    sha256: str
    size: int
    kind: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_path(
        cls, path: Path, *, kind: str, sha256: str, metadata: dict[str, Any] | None = None
    ) -> "Artifact":
        return cls(
            path=str(path),
            sha256=sha256,
            size=path.stat().st_size,
            kind=kind,
            metadata=metadata or {},
        )


class BuildResult(BaseModel):
    core: TargetCore
    fqbn: str
    build_directory: str
    command: CommandResult
    artifacts: list[Artifact]
    properties: dict[str, str] = Field(default_factory=dict)


class ValidationStep(BaseModel):
    name: str
    ok: bool
    operation_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    evidence_paths: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime
    selector: DeviceSelector
    firmware_commit: str | None = None
    steps: list[ValidationStep]
    artifacts: list[Artifact] = Field(default_factory=list)
    restored_original: bool = False
    warnings: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)
