"""MCP tool registry — low-coupling tool registration for MCP server.

Other modules create ``MCPTool`` instances and hand them to
:class:`ToolRegistry` so the MCP server can expose them through the
standard MCP protocol.

Example — a module exposing its capabilities::

    from trading_tools.mcp.registry import MCPTool

    def create_mcp_tools() -> list[MCPTool]:
        return [
            MCPTool(
                name="stock_profile",
                description="Get company profile",
                parameters={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Ticker symbol"},
                    },
                    "required": ["symbol"],
                },
                handler=get_profile,
            ),
        ]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPTool:
    """A tool that can be exposed via the MCP server.

    Attributes:
        name: Unique tool identifier (snake_case recommended).
        description: Human-readable description shown to MCP clients.
        handler: Callable implementing the tool.  May be sync or async.
            The callable's signature should match ``parameters`` — FastMCP
            validates and converts arguments automatically.
        parameters: JSON Schema describing the tool's input parameters.
        tags: Optional labels for grouping / filtering tools.
    """

    name: str
    description: str
    handler: Callable[..., Any] = field(repr=False)
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})
    tags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("MCPTool.name must not be empty")
        if not self.description:
            raise ValueError("MCPTool.description must not be empty")
        if not callable(self.handler):
            raise ValueError(f"MCPTool.handler must be callable, got {type(self.handler)}")


class ToolRegistry:
    """Collects :class:`MCPTool` instances from various modules.

    Thread-safety: not required — registration happens at startup before
    the server begins handling requests.
    """

    def __init__(self) -> None:
        self._tools: dict[str, MCPTool] = {}

    # ── Registration ──────────────────────────────────────────────

    def register(self, tool: MCPTool) -> None:
        """Register a single tool.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool
        logger.debug("Registered MCP tool: %s", tool.name)

    def register_many(self, tools: Iterable[MCPTool]) -> None:
        """Register multiple tools at once."""
        for tool in tools:
            self.register(tool)

    # ── Lookup ────────────────────────────────────────────────────

    def get(self, name: str) -> MCPTool | None:
        """Retrieve a tool by name, or ``None`` if not found."""
        return self._tools.get(name)

    def list_tools(self, *, tag: str | None = None) -> list[MCPTool]:
        """Return all registered tools, optionally filtered by *tag*."""
        tools = list(self._tools.values())
        if tag is not None:
            tools = [t for t in tools if tag in t.tags]
        return tools

    @property
    def tool_names(self) -> list[str]:
        """Sorted list of registered tool names."""
        return sorted(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())
