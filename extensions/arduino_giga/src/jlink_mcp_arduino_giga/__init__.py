"""First-party Arduino GIGA extension for J-Link MCP."""

from .extension import ArduinoGigaExtension

extension = ArduinoGigaExtension()

__all__ = ["ArduinoGigaExtension", "extension"]
