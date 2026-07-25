"""Fail-closed bridge pin ownership and dynamic conflict tracking."""

from __future__ import annotations

from dataclasses import dataclass

from .models import SAFE_GPIO_PINS, validate_safe_pin


class BridgeResourceConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResourceClaim:
    owner: str
    pins: tuple[str, ...]


class BridgeResourceManager:
    """Small host-side mirror of the firmware resource manager for validation."""

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}

    @property
    def safe_pins(self) -> tuple[str, ...]:
        return SAFE_GPIO_PINS

    def claim(self, owner: str, pins: list[str] | tuple[str, ...]) -> ResourceClaim:
        if not owner or len(owner) > 64:
            raise ValueError("resource owner must be a bounded non-empty name")
        labels = tuple(validate_safe_pin(pin) for pin in pins)
        if not labels:
            raise ValueError("a resource claim needs at least one pin")
        if len(set(labels)) != len(labels):
            raise ValueError("a resource claim cannot repeat a pin")
        conflicts = [
            f"{pin} is already owned by {self._owners[pin]}"
            for pin in labels
            if pin in self._owners and self._owners[pin] != owner
        ]
        if conflicts:
            raise BridgeResourceConflict("; ".join(conflicts))
        for pin in labels:
            self._owners[pin] = owner
        return ResourceClaim(owner=owner, pins=labels)

    def release(self, owner: str) -> None:
        for pin in [pin for pin, current in self._owners.items() if current == owner]:
            del self._owners[pin]

    def conflicts(self) -> list[str]:
        return [f"{pin}:{owner}" for pin, owner in sorted(self._owners.items())]
