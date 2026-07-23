"""prediction tools — Polymarket 预测市场 + MCP 注册."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from trading_tools._common import ok, err, get_opener
from trading_tools.mcp.adapters import make_tool
from trading_tools.mcp.registry import MCPTool

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = 30
DEFAULT_LIMIT = 6


# ── 业务逻辑 ──

def _parse_json_list(value: Any) -> list:
    """Gamma encodes ``outcomes``/``outcomePrices`` as JSON-string arrays."""
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []


def _is_forward_looking(market: dict, now: datetime) -> bool:
    """Keep only open markets that resolve in the future."""
    if market.get("closed"):
        return False
    end_date = market.get("endDate")
    if end_date:
        try:
            if datetime.fromisoformat(end_date.replace("Z", "+00:00")) < now:
                return False
        except ValueError:
            pass
    return bool(_parse_json_list(market.get("outcomePrices"))) and bool(
        _parse_json_list(market.get("outcomes"))
    )


def _prediction_market(topic: str, limit: int = DEFAULT_LIMIT) -> str:
    """Return live prediction-market probabilities for an event topic."""
    try:
        params = urllib.parse.urlencode({"q": topic, "limit_per_type": 20})
        url = f"{GAMMA_BASE}/public-search?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with get_opener().open(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        logger.warning("Polymarket search failed for %r: %s", topic, exc)
        return (
            f"Polymarket data is currently unavailable (network error: {exc}). "
            f"Proceed without prediction-market signal for '{topic}'."
        )

    now = datetime.now(timezone.utc)
    candidates = [
        m
        for event in data.get("events", [])
        for m in event.get("markets", [])
        if _is_forward_looking(m, now)
    ]
    candidates.sort(key=lambda m: m.get("volumeNum") or 0, reverse=True)

    header = (
        f'## Polymarket prediction markets: "{topic}"\n'
        f"Live, market-implied probabilities (higher traded volume = deeper, "
        f"more reliable). A probability is the crowd's priced odds of the event, "
        f"not a forecast you should take as certain.\n\n"
    )

    if not candidates:
        return header + (
            f"No open prediction markets matched '{topic}'. Polymarket coverage "
            f"is concentrated in macro, political, geopolitical, and crypto "
            f"events; a specific equity may have none."
        )

    lines: list[str] = []
    for m in candidates[:limit]:
        prices = _parse_json_list(m.get("outcomePrices"))
        outcomes = _parse_json_list(m.get("outcomes"))
        try:
            prob = float(prices[0])
        except (ValueError, IndexError):
            continue
        label = outcomes[0] if outcomes else "Yes"
        volume = m.get("volumeNum") or 0
        end_date = (m.get("endDate") or "")[:10]
        wk = m.get("oneWeekPriceChange")
        wk_str = (
            f", 1-week {wk * 100:+.1f}pp"
            if isinstance(wk, (int, float)) and wk
            else ""
        )
        lines.append(
            f"- **{m.get('question')}** — {label} {prob:.0%} "
            f"(${volume:,.0f} volume, resolves {end_date}{wk_str})"
        )

    if not lines:
        return header + (
            "Matched markets were found but price data could not be parsed. "
            "Try a different or more specific topic."
        )

    return header + "\n".join(lines) + "\n"


# ── MCP 注册 ──

def create_mcp_tools() -> list[MCPTool]:
    return [
        make_tool(
            name="prediction_market",
            description="Polymarket 预测市场概率查询（宏观经济、地缘政治、加密货币等前瞻性事件）",
            handler=_prediction_market,
            tags={"category:macro", "data-source:polymarket"},
            parameters={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "事件关键词，如 'Fed rate cut', 'recession 2026'",
                    },
                    "limit": {"type": "integer", "description": "最大返回市场数（默认6）"},
                },
                "required": ["topic"],
            },
        ),
    ]
