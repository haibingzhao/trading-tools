"""Technical indicators tools — technical_indicators, market_snapshot."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from trading_tools._common import ok, err
from trading_tools.mcp.adapters import make_tool
from trading_tools.mcp.registry import MCPTool


# ── Supported indicators ──────────────────────────────────────────
SUPPORTED_INDICATORS = {
    "close_50_sma", "close_200_sma", "close_10_ema",
    "macd", "macds", "macdh",
    "rsi", "boll", "boll_ub", "boll_lb",
    "atr", "vwma",
}


def _safe_float(v: Any) -> Any:
    """Convert to float; return None for NaN / non-finite."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else round(f, 6)
    except (TypeError, ValueError):
        return None


# ── Internal: fetch OHLCV DataFrame ──────────────────────────────
def _fetch_ohlcv(symbol: str, look_back_days: int, end_date: Optional[str] = None):
    """Return a pandas DataFrame with OHLCV columns for *symbol*."""
    import pandas as pd
    from trading_tools.market_data import fetch_market_data

    if end_date:
        end = end_date
    else:
        # Use last trading day: if today is weekend, roll back to Friday
        today = date.today()
        if today.weekday() == 5:  # Saturday
            today -= timedelta(days=1)
        elif today.weekday() == 6:  # Sunday
            today -= timedelta(days=2)
        end = today.isoformat()

    # Fetch enough history for long-period indicators (e.g. 200-SMA)
    fetch_end = datetime.fromisoformat(end)
    fetch_start = (fetch_end - timedelta(days=max(look_back_days, 300) + 100)).strftime("%Y-%m-%d")

    raw = fetch_market_data(
        codes=[symbol],
        start_date=fetch_start,
        end_date=end,
        source="auto",
        interval="1D",
        max_rows=500,
    )

    if symbol not in raw or not raw[symbol]:
        raise ValueError(f"No data returned for {symbol}")

    records = raw[symbol]
    df = pd.DataFrame(records)

    # Normalise column names (the loader may return various casings)
    col_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if cl in ("date", "datetime", "timestamp"):
            col_map[c] = "date"
        elif cl == "open":
            col_map[c] = "open"
        elif cl == "high":
            col_map[c] = "high"
        elif cl == "low":
            col_map[c] = "low"
        elif cl == "close":
            col_map[c] = "close"
        elif cl in ("volume", "vol"):
            col_map[c] = "volume"
    df = df.rename(columns=col_map)

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    df = df.sort_index()

    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ── Public: _indicators ──────────────────────────────────────────
def _indicators(
    symbol: str,
    indicator_names: str,
    end_date: str | None = None,
    look_back_days: int = 30,
) -> str:
    """Compute requested technical indicators for *symbol*.

    Args:
        symbol: Stock ticker symbol (e.g. AAPL, 600519.SH).
        indicator_names: Comma-separated indicator names.
        end_date: End date in YYYY-MM-DD format. Defaults to today.
        look_back_days: Number of days to look back (default 30).
    """
    try:
        import stockstats

        names = [n.strip() for n in indicator_names.split(",") if n.strip()]
        unsupported = [n for n in names if n not in SUPPORTED_INDICATORS]
        if unsupported:
            return err(f"Unsupported indicators: {unsupported}. Supported: {sorted(SUPPORTED_INDICATORS)}")

        df = _fetch_ohlcv(symbol, look_back_days, end_date)
        sdf = stockstats.wrap(df)

        # Trigger computation for each requested indicator
        for name in names:
            _ = sdf[name]

        # Trim to look_back_days window
        trimmed = sdf.iloc[-look_back_days:]

        # Build date list
        dates: List[str] = []
        for idx in trimmed.index:
            if hasattr(idx, "strftime"):
                dates.append(idx.strftime("%Y-%m-%d"))
            else:
                dates.append(str(idx))

        # Build indicator result dict
        indicators: Dict[str, List[Any]] = {}
        for name in names:
            indicators[name] = [_safe_float(v) for v in trimmed[name].tolist()]

        return ok({"symbol": symbol, "indicators": indicators, "dates": dates})
    except Exception as exc:
        return err(str(exc))


# ── Public: _snapshot ────────────────────────────────────────────
_CORE_INDICATORS = [
    "close_50_sma", "close_200_sma", "close_10_ema",
    "rsi", "boll", "boll_ub", "boll_lb",
    "macd", "macds", "macdh", "atr",
]


def _snapshot(
    symbol: str,
    end_date: str | None = None,
    look_back_days: int = 30,
) -> str:
    """Return a deterministic market snapshot: latest OHLCV + core indicators.

    Args:
        symbol: Stock ticker symbol (e.g. AAPL, 600519.SH).
        end_date: End date in YYYY-MM-DD format. Defaults to today.
        look_back_days: Number of days to look back (default 30).
    """
    try:
        import stockstats

        df = _fetch_ohlcv(symbol, look_back_days, end_date)
        sdf = stockstats.wrap(df)

        # Trigger all core indicators
        for name in _CORE_INDICATORS:
            _ = sdf[name]

        # Latest row
        latest = sdf.iloc[-1]

        # Latest OHLCV
        ohlcv_keys = ["open", "high", "low", "close", "volume"]
        latest_ohlcv = {k: _safe_float(latest.get(k)) for k in ohlcv_keys if k in latest.index}
        # Add date
        idx = sdf.index[-1]
        latest_ohlcv["date"] = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)

        # Latest indicator values
        indicators: Dict[str, Any] = {}
        for name in _CORE_INDICATORS:
            indicators[name] = _safe_float(latest[name])

        # Recent closes
        recent = sdf.iloc[-look_back_days:]
        recent_closes = [_safe_float(v) for v in recent["close"].tolist()]

        return ok({
            "symbol": symbol,
            "latest_ohlcv": latest_ohlcv,
            "indicators": indicators,
            "recent_closes": recent_closes,
        })
    except Exception as exc:
        return err(str(exc))


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def create_mcp_tools() -> list[MCPTool]:
    return [
        make_tool(
            name="technical_indicators",
            description=(
                "计算股票技术指标（RSI/MACD/SMA/Bollinger/ATR等）。"
                "支持的技术指标: close_50_sma, close_200_sma, close_10_ema, "
                "macd, macds, macdh, rsi, boll, boll_ub, boll_lb, atr, vwma"
            ),
            handler=_indicators,
            tags={"market-data", "technical"},
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码 (e.g. AAPL, 600519.SH)",
                    },
                    "indicators": {
                        "type": "string",
                        "description": "逗号分隔的指标名，如 'rsi,macd,close_50_sma'",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "截止日期 YYYY-MM-DD（默认今天）",
                    },
                    "look_back_days": {
                        "type": "integer",
                        "description": "回溯天数（默认 30）",
                        "default": 30,
                    },
                },
                "required": ["symbol", "indicators"],
            },
        ),
        make_tool(
            name="market_snapshot",
            description=(
                "获取股票确定性市场验证快照：最新OHLCV + 11个核心技术指标 "
                "+ 近期收盘价序列。用于验证AI声明的事实准确性。"
            ),
            handler=_snapshot,
            tags={"market-data", "validation"},
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码 (e.g. AAPL, 600519.SH)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "截止日期 YYYY-MM-DD（默认今天）",
                    },
                    "look_back_days": {
                        "type": "integer",
                        "description": "回溯天数（默认 30）",
                        "default": 30,
                    },
                },
                "required": ["symbol"],
            },
        ),
    ]
