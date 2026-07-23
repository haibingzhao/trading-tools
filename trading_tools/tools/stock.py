"""Stock tools — stock_profile, instrument_context."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from trading_tools._common import ok, err, coerce_list
from trading_tools.mcp.adapters import make_tool
from trading_tools.mcp.registry import MCPTool


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

_SECTION_MODULES = {
    "key_stats": "defaultKeyStatistics",
    "financials": "financialData",
    "earnings_trend": "earningsTrend",
    "institution_ownership": "institutionOwnership",
    "insider_holders": "insiderHolders",
    "recommendation_trend": "recommendationTrend",
}

_MAX_ROWS = 25


def _raw(v: Any) -> Any:
    if isinstance(v, dict):
        return v.get("raw")
    return v


def _profile(symbol: str, sections: Optional[List[str]] = None) -> str:
    """Get company profile including key stats, financials, and ownership data.

    Args:
        symbol: Stock ticker symbol (e.g. AAPL, 600519.SH).
        sections: Sections to include: key_stats, financials, earnings_trend, etc.
    """
    try:
        from trading_tools.yahoo_client import get_quote_summary

        if not sections:
            sections = list(_SECTION_MODULES)
        modules = [_SECTION_MODULES[s] for s in sections]
        summary = get_quote_summary(symbol, modules)
        result: Dict[str, Any] = {"ticker": symbol, "sections": {}}
        for s in sections:
            mod = summary.get(_SECTION_MODULES[s]) or {}
            if s == "key_stats":
                result["sections"][s] = {
                    k: _raw(mod.get(k))
                    for k in ("enterpriseValue", "forwardPE", "trailingEps",
                              "forwardEps", "pegRatio", "priceToBook",
                              "profitMargins", "beta", "sharesOutstanding")
                }
            elif s == "financials":
                result["sections"][s] = {
                    k: _raw(mod.get(k))
                    for k in ("currentPrice", "targetMeanPrice", "recommendationKey",
                              "totalRevenue", "revenueGrowth", "operatingMargins",
                              "returnOnEquity", "totalCash", "totalDebt")
                }
            else:
                result["sections"][s] = mod  # pass-through for complex sections
        return ok(result)
    except Exception as exc:
        return err(str(exc))


# ---------------------------------------------------------------------------
# instrument_context
# ---------------------------------------------------------------------------

_DIRTY_VALUES = {None, "", "N/A", "n/a", "null", "None", "N/a", "n/A"}


def _clean(val: Any) -> Optional[str]:
    """Return *val* as a stripped string, or ``None`` if it is a dirty/empty value."""
    if val is None:
        return None
    s = str(val).strip()
    if s in _DIRTY_VALUES:
        return None
    return s


def _instrument_context(symbol: str) -> dict:
    """Resolve deterministic identity metadata for a stock/fund/crypto ticker.

    Fetches ``summaryProfile`` and ``price`` modules from Yahoo Finance in a
    single call and returns a structured dict of identity metadata.

    Args:
        symbol: Ticker symbol (e.g. AAPL, SPY, BTC-USD, 00700.HK).

    Returns:
        A dict with available identity fields (None/empty values are omitted).
        On total failure returns ``{"error": "..."}``.
    """
    try:
        from trading_tools.yahoo_client import get_quote_summary

        summary = get_quote_summary(symbol, ["summaryProfile", "price"])
        price = summary.get("price") or {}
        profile = summary.get("summaryProfile") or {}

        # --- resolve identity fields (with dirty-value cleaning) ---
        name = _clean(price.get("longName")) or _clean(price.get("shortName"))
        exchange = _clean(price.get("exchange"))
        quote_type = (_clean(price.get("quoteType")) or "").upper()
        sector = _clean(profile.get("sector"))
        industry = _clean(profile.get("industry"))

        # --- bail out when Yahoo knows nothing about this ticker ---
        if not any([name, exchange, quote_type, sector, industry]):
            return {"error": f"Unknown ticker '{symbol}': Yahoo Finance returned no identity data."}

        # --- determine asset category ---
        if quote_type == "CRYPTOCURRENCY":
            asset_category = "crypto"
        elif quote_type in ("ETF", "MUTUALFUND"):
            asset_category = "fund"
        else:
            asset_category = "stock"

        # --- build structured result (omit None/empty values) ---
        data: Dict[str, Any] = {"symbol": symbol}

        if name:
            data["name"] = name
        if exchange:
            data["exchange"] = exchange
        if quote_type:
            data["quote_type"] = quote_type
        if sector:
            data["sector"] = sector
        if industry:
            data["industry"] = industry
        data["asset_category"] = asset_category

        return data
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# MCP handler wrappers
# ---------------------------------------------------------------------------

def _mcp_profile(symbol: str, sections: Optional[Any] = None) -> str:
    """Get company profile including key stats, financials, and ownership data.

    Args:
        symbol: Stock ticker symbol (e.g. AAPL, 600519.SH).
        sections: Sections to include: key_stats, financials, earnings_trend, etc.
    """
    return _profile(symbol, coerce_list(sections))


def _mcp_instrument_ctx(symbol: str) -> dict:
    """Resolve deterministic identity metadata for a stock/fund/crypto ticker.

    Returns a structured dict with identity fields (symbol, name, exchange,
    quote_type, sector, industry, asset_category).

    Args:
        symbol: Ticker symbol (e.g. AAPL, SPY, BTC-USD, 00700.HK).
    """
    return _instrument_context(symbol)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def create_mcp_tools() -> list[MCPTool]:
    return [
        make_tool(
            name="stock_profile",
            description="Get company profile including key stats, financials, earnings trend, and ownership data via Yahoo Finance.",
            handler=_mcp_profile,
            tags={"stock", "fundamental"},
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock ticker symbol (e.g. AAPL, 600519.SH)"},
                    "sections": {
                        "type": "array",
                        "description": "Sections to include: key_stats, financials, earnings_trend, institution_ownership, insider_holders, recommendation_trend",
                        "items": {"type": "string"},
                    },
                },
                "required": ["symbol"],
            },
        ),
        make_tool(
            name="instrument_context",
            description="Resolve deterministic identity (name, sector, industry, exchange, quote type) for a ticker via Yahoo Finance. Supports stocks, ETFs/funds, and crypto assets. Returns a structured dict with identity fields. Call this FIRST before other analysis tools.",
            handler=_mcp_instrument_ctx,
            tags={"stock", "identity"},
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Ticker symbol (e.g. AAPL for stocks, SPY for ETFs, BTC-USD for crypto, 00700.HK for HK stocks)",
                    },
                },
                "required": ["symbol"],
            },
        ),
    ]
