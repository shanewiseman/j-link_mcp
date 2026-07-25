"""Target profiles and core-specific J-Link/Arduino configuration."""

from __future__ import annotations

from dataclasses import dataclass

from .models import TargetCore


@dataclass(frozen=True, slots=True)
class CoreProfile:
    core: TargetCore
    jlink_device: str
    arduino_board_options: tuple[str, ...]
    svd_name: str


@dataclass(frozen=True, slots=True)
class TargetProfile:
    name: str
    display_name: str
    fqbn: str
    usb_vid: str
    usb_pids: tuple[str, ...]
    mcu: str
    cores: dict[TargetCore, CoreProfile]
    default_interface: str = "SWD"
    default_speed_khz: int = 4000


GIGA_R1 = TargetProfile(
    name="arduino_giga_r1",
    display_name="Arduino GIGA R1",
    fqbn="arduino:mbed_giga:giga",
    usb_vid="2341",
    usb_pids=("0266", "0366", "0466"),
    mcu="STM32H747XI",
    cores={
        TargetCore.M7: CoreProfile(
            core=TargetCore.M7,
            jlink_device="STM32H747XI_M7",
            arduino_board_options=("target_core=cm7",),
            svd_name="STM32H747_CM7.svd",
        ),
        TargetCore.M4: CoreProfile(
            core=TargetCore.M4,
            jlink_device="STM32H747XI_M4",
            arduino_board_options=("target_core=cm4",),
            svd_name="STM32H747_CM4.svd",
        ),
    },
)

TARGET_PROFILES: dict[str, TargetProfile] = {GIGA_R1.name: GIGA_R1}


def get_profile(name: str) -> TargetProfile:
    try:
        return TARGET_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown target profile: {name}") from exc


def jlink_device(profile_name: str, core: TargetCore) -> str:
    return get_profile(profile_name).cores[core].jlink_device
