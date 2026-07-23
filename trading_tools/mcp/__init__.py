"""MCP sub-package — registry, adapters, config, server."""

from trading_tools.mcp.registry import MCPTool, ToolRegistry
from trading_tools.mcp.adapters import make_tool
from trading_tools.mcp.config import ServerConfig
from trading_tools.mcp.server import MCPServer, create_server

__all__ = [
    "MCPTool",
    "ToolRegistry",
    "make_tool",
    "ServerConfig",
    "MCPServer",
    "create_server",
]
