"""Market data MCP tool — market_data."""

from __future__ import annotations

from typing import Any

from trading_tools._common import coerce_list
from trading_tools.mcp.adapters import make_tool
from trading_tools.mcp.registry import MCPTool


def _fetch(codes: Any, start_date: str, end_date: str,
           source: str = "auto", interval: str = "1D",
           max_rows: int = 500) -> str:
    """Fetch OHLCV market data for multiple symbols from various sources.

    Args:
        codes: List of symbol codes (e.g. ['AAPL', '600519.SH', 'BTC-USDT']).
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        source: Data source: auto, yfinance, baostock, okx, etc. Default: auto.
        interval: Bar interval: 1D, 1H, 30m, 15m, 5m, 1m. Default: 1D.
        max_rows: Max rows per symbol (default 500).
    """
    from trading_tools.market_data import fetch_market_data_json
    return fetch_market_data_json(
        codes=coerce_list(codes), start_date=start_date, end_date=end_date,
        source=source, interval=interval, max_rows=max_rows,
    )


def create_mcp_tools() -> list[MCPTool]:
    return [
        make_tool(
            name="market_data",
            description="Fetch OHLCV market data for multiple symbols from various sources (Yahoo, BaoStock, OKX, CCXT, Eastmoney, etc.). Auto-detects source by symbol format.",
            handler=_fetch,
            tags={"market_data", "ohlcv"},
            parameters={
                "type": "object",
                "properties": {
                    "codes": {
                        "type": "array",
                        "description": "List of symbol codes (e.g. ['AAPL', '600519.SH', 'BTC-USDT'])",
                        "items": {"type": "string"},
                    },
                    "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                    "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                    "source": {"type": "string", "description": "Data source: auto, yfinance, baostock, okx, ccxt, eastmoney, etc. Default: auto", "default": "auto"},
                    "interval": {"type": "string", "description": "Bar interval: 1D, 1H, 30m, 15m, 5m, 1m. Default: 1D", "default": "1D"},
                    "max_rows": {"type": "integer", "description": "Max rows per symbol (default 500)", "default": 500},
                },
                "required": ["codes", "start_date", "end_date"],
            },
        ),
    ]
