"""First-party universal protocol bridge extension for J-Link MCP."""

from .extension import GigaProtocolBridgeExtension

extension = GigaProtocolBridgeExtension()

__all__ = ["GigaProtocolBridgeExtension", "extension"]
