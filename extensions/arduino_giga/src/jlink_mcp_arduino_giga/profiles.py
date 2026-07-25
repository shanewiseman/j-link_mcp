"""Arduino GIGA target profile and build-facing core identifiers."""

from __future__ import annotations

from enum import StrEnum

from jlink_mcp.profiles import CoreProfile, TargetProfile


class TargetCore(StrEnum):
    M7 = "m7"
    M4 = "m4"


GIGA_R1 = TargetProfile(
    id="arduino_giga_r1",
    display_name="Arduino GIGA R1",
    default_core=TargetCore.M7,
    expected_dpidr=0x6BA02477,
    cores={
        TargetCore.M7: CoreProfile(
            id=TargetCore.M7,
            jlink_device="STM32H747XI_M7",
            expected_core="Cortex-M7",
            expected_cpuid=0x411FC271,
            svd_name="STM32H747_CM7.svd",
            metadata={"arduino_board_options": ["target_core=cm7"]},
        ),
        TargetCore.M4: CoreProfile(
            id=TargetCore.M4,
            jlink_device="STM32H747XI_M4",
            expected_core="Cortex-M4",
            expected_cpuid=0x410FC241,
            svd_name="STM32H747_CM4.svd",
            metadata={"arduino_board_options": ["target_core=cm4"]},
        ),
    },
    metadata={
        "fqbn": "arduino:mbed_giga:giga",
        "usb_vid": "2341",
        "usb_pids": ["0266", "0366", "0466"],
        "mcu": "STM32H747XI",
    },
)


def get_profile(name: str) -> TargetProfile:
    if name != GIGA_R1.id:
        raise ValueError(f"unknown Arduino GIGA target profile: {name}")
    return GIGA_R1
