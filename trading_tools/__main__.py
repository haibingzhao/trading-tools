"""CLI entry point for the trading-tools MCP server.

Usage::

    # Default: stdio transport
    python -m trading_tools

    # SSE transport on custom port
    python -m trading_tools --transport sse --host 0.0.0.0 --port 9090

    # Load config from JSON file (CLI args override file values)
    python -m trading_tools --config /path/to/mcp-config.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Modules that may expose a ``create_mcp_tools() -> list[MCPTool]`` function.
_AUTO_DISCOVER_MODULES: list[str] = [
    "trading_tools.tools.stock",
    "trading_tools.tools.fundamental",
    "trading_tools.tools.market_data_tool",
    "trading_tools.tools.indicators",
    "trading_tools.tools.news",
    "trading_tools.tools.sentiment",
    "trading_tools.tools.macro",
    "trading_tools.tools.prediction",
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    prog = "trading-tools-mcp" if Path(sys.argv[0]).name == "trading-tools-mcp" else "python -m trading_tools"
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Trading Tools MCP Server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=None,
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address for HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Listen port for HTTP transports (default: 8080)",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Server name presented to MCP clients",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to a JSON config file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args(argv)


def _load_config_from_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load config from %s: %s", path, exc)
        return {}


def _auto_discover_tools() -> list:
    """Try to import MCP tools from known modules."""
    from trading_tools.mcp.registry import MCPTool

    tools: list[MCPTool] = []
    for module_name in _AUTO_DISCOVER_MODULES:
        try:
            mod = importlib.import_module(module_name)
        except ImportError:
            logger.debug("Module %s not found, skipping", module_name)
            continue
        except Exception as exc:
            logger.warning("Failed to import %s: %s", module_name, exc)
            continue

        factory = getattr(mod, "create_mcp_tools", None)
        if factory is not None and callable(factory):
            try:
                discovered = factory()
                tools.extend(discovered)
                logger.info(
                    "Discovered %d tool(s) from %s",
                    len(discovered),
                    module_name,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to load tools from %s: %s",
                    module_name,
                    exc,
                )

    return tools


def _redirect_futu_logger_to_stderr() -> None:
    """Patch futu's FTLog console handler to use stderr instead of stdout."""
    try:
        from futu.common.ft_logger import logger as ft_logger
        import logging as _logging

        old_handler = ft_logger.consoleHandler
        new_handler = _logging.StreamHandler(sys.stderr)
        new_handler.setLevel(old_handler.level)
        new_handler.setFormatter(old_handler.formatter)

        ft_logger.console_logger.removeHandler(old_handler)
        ft_logger.console_logger.addHandler(new_handler)
        ft_logger.consoleHandler = new_handler
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    """Main entry point for the MCP server CLI."""
    # Load .env before anything else
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass

    args = _parse_args(argv)

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    _redirect_futu_logger_to_stderr()

    # Build config: file → CLI overrides → defaults
    from trading_tools.mcp.config import ServerConfig
    from trading_tools.mcp.server import MCPServer

    file_config = _load_config_from_file(args.config) if args.config else {}

    config = ServerConfig(
        name=args.name or file_config.get("name", "TradingTools"),
        transport=args.transport or file_config.get("transport", "stdio"),
        host=args.host or file_config.get("host", "127.0.0.1"),
        port=args.port or file_config.get("port", 8080),
    )

    server = MCPServer(config)

    # Auto-discover and register tools
    tools = _auto_discover_tools()
    if tools:
        server.register_tools(tools)

    server.run()


if __name__ == "__main__":
    main()
