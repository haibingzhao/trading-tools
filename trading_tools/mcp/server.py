"""MCP Server — expose trading-tools capabilities via MCP protocol.

Built on top of ``fastmcp.FastMCP``, this module provides:

1. A :class:`MCPServer` class that wraps FastMCP with a
   :class:`~trading_tools.mcp.registry.ToolRegistry`-based tool registration flow.
2. A :func:`create_server` factory for one-line server creation.

Other modules expose their capabilities by creating
:class:`~trading_tools.mcp.registry.MCPTool` instances and passing them
to :meth:`MCPServer.register_tools`.

Example::

    from trading_tools.mcp import create_server, MCPTool

    server = create_server()

    server.register_tools([
        MCPTool(
            name="stock_profile",
            description="Get company profile",
            handler=get_profile,
        ),
    ])

    server.run()
"""

from __future__ import annotations

import logging
from typing import Iterable

from fastmcp import FastMCP

from trading_tools.mcp.config import ServerConfig
from trading_tools.mcp.registry import MCPTool, ToolRegistry

logger = logging.getLogger(__name__)


class MCPServer:
    """trading-tools MCP service.

    Wraps a :class:`fastmcp.FastMCP` instance and a :class:`ToolRegistry`
    so that tools can be registered declaratively before the server starts.

    Lifecycle::

        server = MCPServer()               # 1. create
        server.register_tools(tools)        # 2. register tools
        server.run()                        # 3. start (blocks)
    """

    def __init__(self, config: ServerConfig | None = None) -> None:
        """Initialise the MCP server.

        Args:
            config: Server configuration.  Uses defaults when ``None``.
        """
        self._config = config or ServerConfig()
        self._mcp = FastMCP(self._config.name)
        self._registry = ToolRegistry()

    # ── Properties ────────────────────────────────────────────────

    @property
    def config(self) -> ServerConfig:
        """The server configuration."""
        return self._config

    @property
    def registry(self) -> ToolRegistry:
        """The tool registry (read-only access)."""
        return self._registry

    @property
    def mcp(self) -> FastMCP:
        """The underlying :class:`fastmcp.FastMCP` instance.

        Exposed for advanced use cases (e.g. adding resources or prompts
        directly).  Prefer :meth:`register_tools` for normal tool
        registration.
        """
        return self._mcp

    # ── Tool registration ─────────────────────────────────────────

    def register_tools(self, tools: Iterable[MCPTool]) -> None:
        """Register tools and wire them into the MCP server.

        Each tool's ``handler`` is registered with FastMCP via the
        ``@tool`` decorator equivalent (``FastMCP.tool()``).  FastMCP
        introspects the handler's function signature to build the
        input schema automatically.

        Args:
            tools: Iterable of :class:`MCPTool` instances to register.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        for tool in tools:
            # Register in our registry (validates uniqueness)
            self._registry.register(tool)

            # Wire into FastMCP — the decorator returns the original
            # function when called with a callable as first argument.
            self._mcp.tool(
                name=tool.name,
                description=tool.description,
                tags=set(tool.tags) if tool.tags else None,
            )(tool.handler)

            logger.info("Registered MCP tool: %s", tool.name)

    # ── Server lifecycle ──────────────────────────────────────────

    def run(self, **transport_kwargs) -> None:
        """Start the MCP server (blocking).

        Args:
            **transport_kwargs: Extra keyword arguments forwarded to
                ``FastMCP.run()`` (e.g. ``host``, ``port`` for HTTP
                transports).
        """
        tool_count = len(self._registry)
        logger.info(
            "Starting MCP server '%s' (transport=%s, tools=%d)",
            self._config.name,
            self._config.transport,
            tool_count,
        )

        # Inject host/port for HTTP-based transports
        if self._config.transport in ("sse", "streamable-http", "http"):
            transport_kwargs.setdefault("host", self._config.host)
            transport_kwargs.setdefault("port", self._config.port)

        self._mcp.run(
            transport=self._config.transport,
            **transport_kwargs,
        )

    async def run_async(self, **transport_kwargs) -> None:
        """Start the MCP server asynchronously.

        See :meth:`run` for details.
        """
        if self._config.transport in ("sse", "streamable-http", "http"):
            transport_kwargs.setdefault("host", self._config.host)
            transport_kwargs.setdefault("port", self._config.port)

        await self._mcp.run_async(
            transport=self._config.transport,
            **transport_kwargs,
        )


def create_server(config: ServerConfig | None = None) -> MCPServer:
    """Factory: create and return a new :class:`MCPServer`.

    Args:
        config: Optional server configuration.

    Returns:
        A new :class:`MCPServer` instance ready for tool registration.
    """
    return MCPServer(config)
