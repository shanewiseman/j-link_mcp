"""Public API for trusted, explicitly enabled J-Link MCP extensions."""

from .api import (
    EXTENSION_API_VERSION,
    CapabilityContribution,
    Extension,
    ExtensionContext,
    ExtensionError,
    ExtensionServices,
)
from .loader import ExtensionManager

__all__ = [
    "EXTENSION_API_VERSION",
    "CapabilityContribution",
    "Extension",
    "ExtensionContext",
    "ExtensionError",
    "ExtensionManager",
    "ExtensionServices",
]
