"""Target-neutral target profile contracts and runtime-local registration."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .models import BoardCapabilities, USBDevice, normalize_selector_identifier


def _require_canonical_identifier(value: Any, kind: str) -> str:
    normalized = normalize_selector_identifier(value)
    if not isinstance(normalized, str) or normalized != value:
        raise ValueError(f"{kind} must use the canonical selector identifier format")
    return normalized


@dataclass(frozen=True, slots=True)
class CoreProfile:
    """One profile-defined target core exposed through J-Link."""

    id: str
    jlink_device: str
    expected_core: str
    expected_cpuid: int
    svd_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_canonical_identifier(self.id, "core profile id")
        if not self.jlink_device:
            raise ValueError("core profile jlink_device must not be empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """Positive-identity requirements and defaults for one target family."""

    id: str
    display_name: str
    cores: Mapping[str, CoreProfile]
    default_core: str
    expected_dpidr: int
    minimum_target_voltage: float = 1.0
    default_interface: str = "SWD"
    default_speed_khz: int = 4000
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        cores = dict(self.cores)
        _require_canonical_identifier(self.id, "target profile id")
        if not cores:
            raise ValueError("target profile must declare at least one core")
        if self.default_core not in cores:
            raise ValueError("target profile default_core is not registered")
        if (
            not math.isfinite(self.minimum_target_voltage)
            or self.minimum_target_voltage <= 0
        ):
            raise ValueError(
                "target profile minimum_target_voltage must be finite and positive"
            )
        if not self.default_interface.strip():
            raise ValueError("target profile default_interface must not be empty")
        if not 5 <= self.default_speed_khz <= 50000:
            raise ValueError(
                "target profile default_speed_khz must be between 5 and 50000"
            )
        for core_id, profile in cores.items():
            _require_canonical_identifier(core_id, "target profile core key")
            if core_id != profile.id:
                raise ValueError("target profile core key must match CoreProfile.id")
        object.__setattr__(self, "cores", MappingProxyType(cores))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


BoardDetector = Callable[[USBDevice], BoardCapabilities | None]


class TargetRegistry:
    """Profiles and detectors registered for one MCP runtime."""

    def __init__(self) -> None:
        self._profiles: dict[str, TargetProfile] = {}
        self._detectors: dict[str, BoardDetector] = {}

    @property
    def profiles(self) -> Mapping[str, TargetProfile]:
        return MappingProxyType(self._profiles)

    @property
    def detectors(self) -> Mapping[str, BoardDetector]:
        return MappingProxyType(self._detectors)

    def register_profile(self, profile: TargetProfile) -> None:
        if profile.id in self._profiles:
            raise ValueError(f"duplicate target profile: {profile.id}")
        self._profiles[profile.id] = profile

    def register_board_detector(
        self, detector_id: str, detector: BoardDetector
    ) -> None:
        if detector_id in self._detectors:
            raise ValueError(f"duplicate board detector: {detector_id}")
        self._detectors[detector_id] = detector

    def unregister_profile(self, profile_id: str) -> None:
        self._profiles.pop(profile_id, None)

    def unregister_board_detector(self, detector_id: str) -> None:
        self._detectors.pop(detector_id, None)

    def get_profile(self, profile_id: str) -> TargetProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise ValueError(f"unknown target profile: {profile_id}") from exc

    def jlink_device(self, profile_id: str | None, core_id: str | None) -> str:
        if profile_id is None or core_id is None:
            raise ValueError("target profile and core must be resolved")
        profile = self.get_profile(profile_id)
        try:
            return profile.cores[core_id].jlink_device
        except KeyError as exc:
            raise ValueError(
                f"unknown core {core_id!r} for target profile {profile_id}"
            ) from exc

    def detect_board(self, usb: USBDevice) -> BoardCapabilities | None:
        matches = [
            board
            for detector in self._detectors.values()
            if (board := detector(usb)) is not None
        ]
        if len(matches) > 1:
            raise ValueError(
                "multiple board detectors matched USB device "
                f"{usb.vendor_id}:{usb.product_id}"
            )
        return matches[0] if matches else None
