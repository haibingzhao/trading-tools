"""macro tools — FRED 经济数据 + MCP 注册."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, date
from typing import Any

from trading_tools._common import ok, err, get_opener, coerce_list
from trading_tools.mcp.adapters import make_tool
from trading_tools.mcp.registry import MCPTool

logger = logging.getLogger(__name__)

# ── 业务逻辑 ──

_FRED_API = "https://api.stlouisfed.org/fred"


def _fred_key() -> str | None:
    key = os.environ.get("FRED_API_KEY", "").strip()
    return key or None


def _fred_search(
    query: str,
    limit: int = 10,
    order_by: str = "search_rank",
    sort_order: str = "desc",
) -> str:
    """Search for FRED economic data series."""
    api_key = _fred_key()
    if not api_key:
        return err("FRED_API_KEY not configured")

    params = urllib.parse.urlencode({
        "search_text": query,
        "api_key": api_key,
        "file_type": "json",
        "order_by": order_by,
        "sort_order": sort_order,
        "limit": limit,
    })
    url = f"{_FRED_API}/series/search?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with get_opener().open(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return err(f"FRED search error: {exc}")

    series_list = data.get("seriess", [])
    results = []
    for s in series_list:
        results.append({
            "id": s.get("id", ""),
            "title": s.get("title", ""),
            "observation_start": s.get("observation_start", ""),
            "observation_end": s.get("observation_end", ""),
            "frequency": s.get("frequency", ""),
            "units": s.get("units", ""),
            "seasonal_adjustment": s.get("seasonal_adjustment", ""),
            "popularity": s.get("popularity", 0),
        })
    return ok({"count": len(results), "series": results})


def _fred_series(
    series_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    frequency: str | None = None,
    aggregation_method: str | None = None,
    units: str | None = None,
    limit: int = 100,
) -> str:
    """Fetch FRED economic data series observations."""
    api_key = _fred_key()
    if not api_key:
        return err("FRED_API_KEY not configured")

    if not series_id or len(series_id) < 2:
        return err("Invalid series_id")

    params: dict[str, Any] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": limit,
        "sort_order": "desc",
    }
    if start_date:
        params["observation_start"] = start_date
    if end_date:
        params["observation_end"] = end_date
    if frequency:
        params["frequency"] = frequency
    if aggregation_method:
        params["aggregation_method"] = aggregation_method
    if units:
        params["units"] = units

    url = f"{_FRED_API}/series/observations?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with get_opener().open(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return err(f"FRED series error: {exc}")

    observations = data.get("observations", [])
    result = []
    for obs in observations:
        result.append({
            "date": obs.get("date", ""),
            "value": obs.get("value", ""),
        })

    # Also fetch series info
    info_params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    })
    info_url = f"{_FRED_API}/series?{info_params}"
    info_req = urllib.request.Request(info_url, headers={"Accept": "application/json"})
    title = series_id
    try:
        with get_opener().open(info_req, timeout=10) as resp:
            info_data = json.loads(resp.read())
        series_info = info_data.get("seriess", [{}])[0]
        title = series_info.get("title", series_id)
    except Exception:
        pass

    return ok({
        "series_id": series_id,
        "title": title,
        "count": len(result),
        "observations": result,
    })


def _fred_release(
    release_id: int,
    limit: int = 50,
) -> str:
    """Fetch FRED release data (e.g., CPI, GDP reports)."""
    api_key = _fred_key()
    if not api_key:
        return err("FRED_API_KEY not configured")

    params = urllib.parse.urlencode({
        "release_id": release_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": limit,
        "sort_order": "desc",
    })
    url = f"{_FRED_API}/release/dates?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with get_opener().open(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return err(f"FRED release error: {exc}")

    dates = data.get("release_dates", [])
    result = []
    for d in dates:
        result.append({
            "date": d.get("date", ""),
            "release_id": d.get("release_id", 0),
            "release_name": d.get("release_name", ""),
        })
    return ok({"release_id": release_id, "count": len(result), "dates": result})


def _fred_releases(limit: int = 20) -> str:
    """List upcoming FRED data releases."""
    api_key = _fred_key()
    if not api_key:
        return err("FRED_API_KEY not configured")

    today = date.today().strftime("%Y-%m-%d")
    params = urllib.parse.urlencode({
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": today,
        "realtime_end": today,
        "limit": limit,
    })
    url = f"{_FRED_API}/releases/dates?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with get_opener().open(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        return err(f"FRED releases error: {exc}")

    releases = data.get("releases", [])
    result = []
    for r in releases:
        result.append({
            "id": r.get("id", 0),
            "name": r.get("name", ""),
            "press_release": r.get("press_release", False),
            "link": r.get("link", ""),
            "notes": r.get("notes", ""),
        })
    return ok({"count": len(result), "releases": result})


# ── MCP 注册 ──

def create_mcp_tools() -> list[MCPTool]:
    return [
        make_tool(
            name="fred_data",
            description="查询 FRED 经济数据：search=搜索序列，series=获取数据，releases=即将发布，release=历史发布",
            handler=_handle_fred_data,
            tags={"category:finance", "data-source:fred"},
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "操作：search, series, releases, release",
                        "enum": ["search", "series", "releases", "release"],
                    },
                    "query": {"type": "string", "description": "搜索关键词 (action=search)"},
                    "series_id": {"type": "string", "description": "序列ID (action=series), e.g. GDPC1, CPIAUCSL, UNRATE, DFF"},
                    "release_id": {"type": "integer", "description": "发布ID (action=release)"},
                    "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                    "frequency": {
                        "type": "string",
                        "description": "频率 (d,w,bw,m,q,sa,a)",
                        "enum": ["d", "w", "bw", "m", "q", "sa", "a"],
                    },
                    "aggregation_method": {
                        "type": "string",
                        "description": "聚合方法",
                        "enum": ["avg", "sum", "eop"],
                    },
                    "units": {"type": "string", "description": "单位变换 (lin,chg,ch1,pch,pc1,pca,cch,cca,log)"},
                    "limit": {"type": "integer", "description": "返回条数，默认 20"},
                    "order_by": {
                        "type": "string",
                        "description": "排序字段 (search)",
                        "enum": ["search_rank", "popularity", "title", "units", "frequency"],
                    },
                },
                "required": ["action"],
            },
        ),
    ]


def _handle_fred_data(
    action: str,
    query: str | None = None,
    series_id: str | None = None,
    release_id: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    frequency: str | None = None,
    aggregation_method: str | None = None,
    units: str | None = None,
    limit: int = 20,
    order_by: str = "search_rank",
) -> str:
    if action == "search":
        if not query:
            return err("query is required for action=search")
        return _fred_search(query, limit=limit, order_by=order_by)
    elif action == "series":
        if not series_id:
            return err("series_id is required for action=series")
        return _fred_series(
            series_id,
            start_date=start_date, end_date=end_date,
            frequency=frequency, aggregation_method=aggregation_method,
            units=units, limit=limit,
        )
    elif action == "releases":
        return _fred_releases(limit=limit)
    elif action == "release":
        if not release_id:
            return err("release_id is required for action=release")
        return _fred_release(release_id, limit=limit)
    return err(f"Unknown action: {action}")
