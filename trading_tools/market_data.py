"""Market data fetcher – unified OHLCV loader.

Self-contained implementation using yahoo_client and optional futu SDK.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 500


def _detect_source(code: str) -> str:
    """Auto-detect data source from symbol format."""
    c = code.upper()
    if c.endswith(".HK"):
        return "futu"
    if c.endswith((".SH", ".SZ", ".BJ")):
        return "futu"
    if "-USDT" in c or "-USD" in c or c.endswith("-SPOT"):
        return "crypto"
    return "yfinance"


def _json_safe(value: Any) -> Any:
    """Convert non-serialisable scalars to JSON-safe primitives."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def _cap_rows(records: List[Dict[str, Any]], max_rows: int) -> List[Dict[str, Any]]:
    if max_rows <= 0 or len(records) <= max_rows:
        return records
    return records[-max_rows:]


def _to_epoch(date_str: str) -> int:
    """Parse YYYY-MM-DD to epoch seconds."""
    return int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())


# ── Yahoo Finance (via yahoo_client) ──

def _fetch_yahoo(codes: List[str], start_date: str, end_date: str,
                 interval: str = "1d") -> Dict[str, List[Dict[str, Any]]]:
    """Fetch OHLCV from Yahoo Finance chart API."""
    from trading_tools.yahoo_client import get_chart, map_symbol

    results: Dict[str, List[Dict[str, Any]]] = {}
    p1 = _to_epoch(start_date)
    p2 = _to_epoch(end_date)
    interval_map = {"1D": "1d", "1d": "1d", "1H": "1h", "1h": "1h",
                    "5m": "5m", "15m": "15m", "30m": "30m", "1wk": "1wk"}
    yf_interval = interval_map.get(interval, "1d")

    for code in codes:
        try:
            rows = get_chart(code, interval=yf_interval, period1=p1, period2=p2)
            if rows:
                # Normalize to standard field names
                normalized = []
                for r in rows:
                    normalized.append({
                        "date": r.get("trade_date"),
                        "open": r.get("open"),
                        "high": r.get("high"),
                        "low": r.get("low"),
                        "close": r.get("close"),
                        "volume": r.get("volume"),
                    })
                results[code] = normalized
        except Exception:
            logger.warning("Yahoo chart failed for %s", code, exc_info=True)
    return results


# ── Futu SDK (optional) ──

def _fetch_futu(codes: List[str], start_date: str, end_date: str,
                interval: str = "1D") -> Dict[str, List[Dict[str, Any]]]:
    """Fetch OHLCV from Futu OpenD SDK."""
    try:
        import futu  # type: ignore
    except ImportError:
        logger.debug("futu package not installed, skipping futu source")
        return {}

    import os, socket
    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    try:
        with socket.create_connection((host, port), timeout=1):
            pass
    except OSError:
        logger.debug("Futu OpenD not reachable")
        return {}

    from trading_tools.tools.stock import _to_futu_symbol

    ctx = None
    results: Dict[str, List[Dict[str, Any]]] = {}
    ktype_map = {
        "1D": futu.KLType.K_DAY, "1d": futu.KLType.K_DAY,
        "1H": futu.KLType.K_60M, "1h": futu.KLType.K_60M,
        "30m": futu.KLType.K_30M, "15m": futu.KLType.K_15M,
        "5m": futu.KLType.K_5M,
    }
    ktype = ktype_map.get(interval, futu.KLType.K_DAY)

    try:
        ctx = futu.OpenQuoteContext(host=host, port=port)
        for code in codes:
            futu_code = _to_futu_symbol(code)
            try:
                ret, data, _ = ctx.request_history_kline(
                    futu_code, start=start_date, end=end_date, ktype=ktype,
                    max_count=DEFAULT_MAX_ROWS, autype=futu.AuType.QFQ,
                )
                if ret == futu.RET_OK and data is not None and not data.empty:
                    records = []
                    for _, row in data.iterrows():
                        records.append({
                            "date": str(row.get("time_key", "")),
                            "open": float(row.get("open", 0)),
                            "high": float(row.get("high", 0)),
                            "low": float(row.get("low", 0)),
                            "close": float(row.get("close", 0)),
                            "volume": int(row.get("volume", 0)),
                        })
                    results[code] = records
            except Exception:
                logger.warning("Futu fetch failed for %s", code, exc_info=True)
    except Exception:
        logger.warning("Futu context error", exc_info=True)
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
    return results


# ── Crypto via CCXT (optional, best-effort) ──

def _fetch_crypto(codes: List[str], start_date: str, end_date: str,
                  interval: str = "1D") -> Dict[str, List[Dict[str, Any]]]:
    """Fetch OHLCV from crypto exchanges via CCXT."""
    try:
        import ccxt  # type: ignore
    except ImportError:
        logger.debug("ccxt not installed, skipping crypto source")
        return {}

    results: Dict[str, List[Dict[str, Any]]] = {}
    tf_map = {"1D": "1d", "1d": "1d", "1H": "1h", "1h": "1h",
              "5m": "5m", "15m": "15m", "30m": "30m"}
    timeframe = tf_map.get(interval, "1d")
    since = _to_epoch(start_date) * 1000  # ccxt uses milliseconds

    for code in codes:
        try:
            # BTC-USDT → BTC/USDT
            symbol = code.replace("-USDT", "/USDT").replace("-USD", "/USD")
            exchange_id = "binance"
            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=DEFAULT_MAX_ROWS)
            records = []
            for candle in ohlcv:
                records.append({
                    "date": candle[0],
                    "open": candle[1],
                    "high": candle[2],
                    "low": candle[3],
                    "close": candle[4],
                    "volume": candle[5],
                })
            if records:
                results[code] = records
        except Exception:
            logger.warning("Crypto fetch failed for %s", code, exc_info=True)
    return results


# ── Fetcher registry ──

_FETCHERS = {
    "yfinance": _fetch_yahoo,
    "yahoo": _fetch_yahoo,
    "futu": _fetch_futu,
    "crypto": _fetch_crypto,
}

_FALLBACK_CHAINS: Dict[str, List[str]] = {
    "yfinance": ["yahoo"],
    "futu": ["yfinance"],
    "crypto": [],
}


def fetch_market_data(
    codes: List[str],
    start_date: str,
    end_date: str,
    source: str = "auto",
    interval: str = "1D",
    max_rows: int = DEFAULT_MAX_ROWS,
) -> Dict[str, Any]:
    """Fetch OHLCV data for *codes* and return a ``{symbol: [rows]}`` mapping."""
    use_auto = source == "auto"

    groups: Dict[str, List[str]] = {}
    for code in codes:
        src = source if not use_auto else _detect_source(code)
        groups.setdefault(src, []).append(code)

    results: Dict[str, Any] = {}

    for src, src_codes in groups.items():
        fetcher = _FETCHERS.get(src)
        if fetcher is None:
            logger.warning("Unknown source %r; codes %s unresolved", src, src_codes)
            results.setdefault("_unresolved", []).extend(src_codes)
            continue

        data_map = fetcher(src_codes, start_date, end_date, interval)

        resolved = set()
        for symbol, records in data_map.items():
            for row in records:
                for key, value in row.items():
                    row[key] = _json_safe(value)
            results[symbol] = _cap_rows(records, max_rows)
            resolved.add(symbol)

        failed_codes = [c for c in src_codes if c not in resolved]

        if use_auto and failed_codes:
            fallbacks = _FALLBACK_CHAINS.get(src, [])
            for fb_src in fallbacks:
                if not failed_codes:
                    break
                fb_fetcher = _FETCHERS.get(fb_src)
                if fb_fetcher is None:
                    continue
                logger.info("Trying fallback %r for %s (primary=%r)", fb_src, failed_codes, src)
                fb_data = fb_fetcher(failed_codes, start_date, end_date, interval)
                newly_resolved = set()
                for symbol, records in fb_data.items():
                    for row in records:
                        for key, value in row.items():
                            row[key] = _json_safe(value)
                    results[symbol] = _cap_rows(records, max_rows)
                    newly_resolved.add(symbol)
                if newly_resolved:
                    failed_codes = [c for c in failed_codes if c not in newly_resolved]

    unresolved = [code for code in codes if code not in results]
    if unresolved:
        results["_unresolved"] = unresolved

    return results


def fetch_market_data_json(**kwargs: Any) -> str:
    """Fetch market data and return strict JSON."""
    return json.dumps(fetch_market_data(**kwargs), ensure_ascii=False, indent=2, allow_nan=False)
