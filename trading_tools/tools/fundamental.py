"""Fundamental tools — sector_info, financial_statements."""

from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import urllib.request
from typing import Any, Dict, List, Optional

from trading_tools._common import ok, err
from trading_tools.mcp.adapters import make_tool
from trading_tools.mcp.registry import MCPTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# statements
# ---------------------------------------------------------------------------

# Finnhub series keys mapped to statement types
_FINNHUB_SERIES: Dict[str, List[str]] = {
    "income":  ["eps", "ebitda", "ebitPerShare", "grossMargin",
                "netProfitMargin", "operatingMargin", "revenueGrowth",
                "roa", "roe", "roic"],
    "balance": ["bookValue", "currentRatio", "cashRatio", "debtToEquity"],
    "cashflow": ["fcfMargin", "cashRatio", "currentRatio"],
}


def _finnhub_fallback(symbol: str, statement_type: str,
                      period: str, limit: int) -> Optional[str]:
    """Try Finnhub basic-financials as fallback for financial statements.

    Returns a JSON string on success, or None if Finnhub also fails.
    """
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        return None
    try:
        freq = "quarterly" if period == "quarterly" else "annual"
        url = (f"https://finnhub.io/api/v1/stock/metric"
               f"?symbol={symbol}&metric=all&token={api_key}")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        handlers: list = [urllib.request.HTTPSHandler(context=ctx)]
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            handlers.insert(0, urllib.request.ProxyHandler(
                {"http": proxy, "https": proxy}))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode())

        series_data = raw.get("series", {}).get(freq, {})
        if not series_data:
            return None

        wanted_keys = _FINNHUB_SERIES.get(statement_type, [])
        statements: List[Dict[str, Any]] = []
        for key in wanted_keys:
            items = series_data.get(key)
            if not items:
                continue
            for item in items[:limit]:
                period_label = item.get("period", "")
                # Find or create period dict
                stmt = next((s for s in statements if s.get("period") == period_label), None)
                if stmt is None:
                    stmt = {"period": period_label}
                    statements.append(stmt)
                stmt[key] = item.get("v")

        if not statements:
            return None

        return ok({
            "symbol": symbol,
            "type": statement_type,
            "period": period,
            "source": "finnhub",
            "statements": statements[:limit],
        })
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Futu SDK fallback for financial statements
# ---------------------------------------------------------------------------

def _to_futu_symbol(symbol: str) -> str:
    """Convert a standard ticker to Futu OpenAPI format.

    Rules:
        09988.HK / 700.HK  -> HK.09988 / HK.00700
        000001.SZ           -> SZ.000001
        600519.SH           -> SH.600519
        BABA / AAPL         -> US.BABA / US.AAPL
    """
    s = symbol.strip().upper()

    # Already in Futu format
    if "." in s and s.split(".")[0] in ("US", "HK", "SZ", "SH", "SG", "JP"):
        return s

    # Hong Kong: 700.HK, 0700.HK, 09988.HK -> HK.{zero-padded 5}
    if s.endswith(".HK"):
        return f"HK.{s[:-3].zfill(5)}"
    # Shenzhen: 000001.SZ -> SZ.000001
    if s.endswith(".SZ"):
        return f"SZ.{s[:-3].zfill(6)}"
    # Shanghai: 600519.SH -> SH.600519
    if s.endswith(".SH"):
        return f"SH.{s[:-3].zfill(6)}"

    # Pure digit heuristics
    if s.isdigit():
        if len(s) == 6 and s[0] == "6":
            return f"SH.{s}"
        if len(s) == 6 and s[0] in ("0", "3"):
            return f"SZ.{s}"
        if len(s) == 5:
            return f"HK.{s}"

    # Default: US market
    return f"US.{s}"


def _futu_opend_reachable() -> bool:
    """Check if Futu OpenD is reachable on the configured host/port."""
    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


# Futu statement_type -> SDK integer
_FUTU_STMT_TYPE_MAP = {
    "income": 1,    # 利润表
    "balance": 2,   # 资产负债表
    "cashflow": 3,  # 现金流量表
}

# Futu display_name -> standard English key (for common financial fields)
_FUTU_FIELD_NAME_MAP: Dict[str, str] = {
    # Income statement
    "收入": "revenue",
    "營業收入": "revenue",
    "銷售成本": "costOfRevenue",
    "營業成本": "costOfRevenue",
    "毛利": "grossProfit",
    "毛利潤": "grossProfit",
    "營業利潤": "operatingIncome",
    "營業利潤率": "operatingMargin",
    "淨利潤": "netIncome",
    "歸屬於母公司淨利潤": "netIncome",
    "歸屬於母公司股東的淨利潤": "netIncome",
    "基本每股收益": "eps",
    "每股基本盈利": "eps",
    "稀釋每股收益": "epsDiluted",
    "每股稀釋盈利": "epsDiluted",
    "EBITDA": "ebitda",
    "息稅折舊及攤銷前利潤": "ebitda",
    "除稅前溢利": "pretaxIncome",
    "除稅前利潤": "pretaxIncome",
    "所得稅": "incomeTax",
    "所得稅開支": "incomeTax",
    "銷售及分銷開支": "sellingExpenses",
    "銷售費用": "sellingExpenses",
    "行政開支": "adminExpenses",
    "管理費用": "adminExpenses",
    "研發開支": "rdExpenses",
    "研發費用": "rdExpenses",
    # Balance sheet
    "總資產": "totalAssets",
    "資產總額": "totalAssets",
    "總負債": "totalLiabilities",
    "負債總額": "totalLiabilities",
    "股東權益": "totalEquity",
    "歸屬於母公司股東權益": "totalEquity",
    "流動資產": "currentAssets",
    "流動負債": "currentLiabilities",
    "現金及現金等價物": "cash",
    "銀行結餘及現金": "cash",
    "現金及銀行結餘": "cash",
    # Cashflow
    "經營活動現金流量淨額": "operatingCashFlow",
    "經營活動所產生的現金淨額": "operatingCashFlow",
    "投資活動現金流量淨額": "investingCashFlow",
    "投資活動所產生的現金淨額": "investingCashFlow",
    "融資活動現金流量淨額": "financingCashFlow",
    "融資活動所產生的現金淨額": "financingCashFlow",
    "自由現金流": "freeCashFlow",
}


def _futu_fallback(symbol: str, statement_type: str,
                   period: str, limit: int) -> Optional[str]:
    """Try Futu OpenD SDK as a third fallback for financial statements.

    Requires ``futu-api`` installed and OpenD running locally.
    Returns a JSON string on success, or None on any failure.
    """
    futu_stmt = _FUTU_STMT_TYPE_MAP.get(statement_type)
    if futu_stmt is None:
        logger.info("[Futu] unsupported statement_type: %s", statement_type)
        return None

    try:
        import futu  # type: ignore  # noqa: PLC0415
    except ImportError:
        logger.info("[Futu] futu-api not installed, skipping")
        return None

    if not _futu_opend_reachable():
        logger.info("[Futu] OpenD not reachable, skipping")
        return None

    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    futu_code = _to_futu_symbol(symbol)
    ctx = None

    try:
        ctx = futu.OpenQuoteContext(host=host, port=port)
        if not hasattr(ctx, "get_financials_statements"):
            logger.info("[Futu] get_financials_statements not available")
            return None

        # financial_type: 7=annual, 9=single-quarter combo (Q1/Q2/Q3/Q4)
        fin_type = 7 if period == "annual" else 9

        ret, data = ctx.get_financials_statements(
            futu_code,
            statement_type=futu_stmt,
            financial_type=fin_type,
            num=max(limit, 10),
        )
        if ret != futu.RET_OK or data is None:
            logger.info("[Futu] API error for %s: %s", futu_code, data)
            return None

        if isinstance(data, dict) and not data.get("report_list"):
            return None

        structure_list = data.get("structure_list", [])
        report_list = data.get("report_list", [])

        # Build field_id -> standard_key mapping from display names
        id_to_key: Dict[int, str] = {}
        for entry in structure_list:
            fid = entry.get("field_id")
            display = entry.get("display_name", "")
            mapped = _FUTU_FIELD_NAME_MAP.get(display)
            if mapped:
                id_to_key[fid] = mapped
            else:
                # Keep original display name for unmapped fields
                id_to_key[fid] = display

        # Transform each report period into a flat dict
        statements: List[Dict[str, Any]] = []
        for rpt in report_list[:limit]:
            stmt: Dict[str, Any] = {
                "period": rpt.get("period_text") or rpt.get("date_time_str") or "",
            }
            for item in rpt.get("item_list", []):
                fid = item.get("field_id")
                key = id_to_key.get(fid)
                if key is None:
                    continue
                stmt[key] = item.get("data")
            statements.append(stmt)

        if not statements:
            return None

        return ok({
            "symbol": symbol,
            "type": statement_type,
            "period": period,
            "source": "futu",
            "statements": statements,
        })
    except Exception as exc:
        logger.info("[Futu] fallback failed for %s: %s", symbol, exc)
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def _statements(symbol: str, statement_type: str = "income",
                period: str = "annual", limit: int = 4) -> str:
    """Fetch financial statements from Yahoo Finance, with Finnhub and Futu fallback.

    Args:
        symbol: Stock ticker symbol.
        statement_type: Statement type: income, balance, cashflow. Default: income.
        period: Period: annual or quarterly. Default: annual.
        limit: Max number of periods (default 4).
    """
    # --- primary: Yahoo Finance ---
    try:
        from trading_tools.yahoo_client import get_quote_summary
        module_map = {
            "income": "incomeStatementHistory",
            "balance": "balanceSheetHistory",
            "cashflow": "cashflowStatementHistory",
        }
        quarter_map = {
            "income": "incomeStatementHistoryQuarterly",
            "balance": "balanceSheetHistoryQuarterly",
            "cashflow": "cashflowStatementHistoryQuarterly",
        }
        mod = module_map.get(statement_type, module_map["income"])
        if period == "quarterly":
            mod = quarter_map.get(statement_type, mod)
        summary = get_quote_summary(symbol, [mod])
        history = summary.get(mod, {})
        stmts = history.get("history", []) if isinstance(history, dict) else []
        if stmts:
            return ok({"symbol": symbol, "type": statement_type, "period": period,
                       "statements": stmts[:limit]})
    except Exception:
        pass

    # --- fallback 1: Finnhub basic-financials ---
    fb = _finnhub_fallback(symbol, statement_type, period, limit)
    if fb is not None:
        return fb

    # --- fallback 2: Futu OpenD SDK (supports HK/A-share natively) ---
    futu_fb = _futu_fallback(symbol, statement_type, period, limit)
    if futu_fb is not None:
        return futu_fb

    return err(f"All sources failed (Yahoo Finance, Finnhub, Futu) for {symbol} {statement_type}")


# ---------------------------------------------------------------------------
# sector
# ---------------------------------------------------------------------------

def _sector(symbol: str) -> str:
    """Get sector and industry classification for a stock.

    Args:
        symbol: Stock ticker symbol.
    """
    try:
        from trading_tools.yahoo_client import get_quote_summary
        summary = get_quote_summary(symbol, ["summaryProfile"])
        profile = summary.get("summaryProfile", {})
        return ok({
            "symbol": symbol,
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
        })
    except Exception as exc:
        return err(str(exc))


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def create_mcp_tools() -> list[MCPTool]:
    return [
        make_tool(
            name="financial_statements",
            description="Fetch financial statements (income/balance/cashflow) for a stock. Sources: Yahoo Finance → Finnhub → Futu SDK.",
            handler=_statements,
            tags={"fundamental", "financial"},
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock ticker symbol"},
                    "statement_type": {"type": "string", "description": "Statement type: income, balance, cashflow. Default: income", "default": "income"},
                    "period": {"type": "string", "description": "Period: annual or quarterly. Default: annual", "default": "annual"},
                    "limit": {"type": "integer", "description": "Max number of periods (default 4)", "default": 4},
                },
                "required": ["symbol"],
            },
        ),
        make_tool(
            name="sector_info",
            description="Get sector and industry classification for a stock via Yahoo Finance.",
            handler=_sector,
            tags={"fundamental", "sector"},
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock ticker symbol"},
                },
                "required": ["symbol"],
            },
        ),
    ]
