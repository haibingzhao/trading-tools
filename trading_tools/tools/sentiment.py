"""sentiment tools — 社交情绪（StockTwits + Reddit + Futu）+ MCP 注册."""

from __future__ import annotations

import html
import http.client
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request

from trading_tools._common import ok as _ok, err as _err, get_opener
from trading_tools.mcp.adapters import make_tool
from trading_tools.mcp.registry import MCPTool

logger = logging.getLogger(__name__)

# ── Constants ──

_STOCKTWITS_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_STOCKTWITS_UA = "easy-trading/1.0"

_REDDIT_RSS = "https://www.reddit.com/r/{sub}/search.rss?{qs}"
_REDDIT_UA = "easy-trading/1.0 (+https://github.com/easy-trading)"
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
DEFAULT_SUBREDDITS = ("wallstreetbets", "stocks", "investing")

_FUTU_COMMUNITY_API = "https://ai-news-search.futunn.com/stock_feed"
_FUTU_COMMUNITY_UA = "futu-comment-sentiment/0.0.2 (easy-trading)"


# ── StockTwits ──

def _stocktwits(symbol: str, limit: int = 30) -> str:
    ticker = symbol.strip().upper()
    url = _STOCKTWITS_API.format(ticker=ticker)
    req = Request(url, headers={"User-Agent": _STOCKTWITS_UA, "Accept": "application/json"})
    try:
        with get_opener().open(req, timeout=10.0) as resp:
            data = json.loads(resp.read())
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        logger.warning("StockTwits fetch failed for %s: %s", ticker, exc)
        return _err(f"stocktwits unavailable: {type(exc).__name__}")

    messages = data.get("messages", []) if isinstance(data, dict) else []
    if not messages:
        return _ok({
            "source": "stocktwits", "symbol": ticker,
            "summary": f"No StockTwits messages found for ${ticker}",
            "bullish": 0, "bearish": 0, "messages": [],
        })

    msg_list = []
    bullish = bearish = unlabeled = 0
    for m in messages[:limit]:
        created = m.get("created_at", "")
        user = (m.get("user") or {}).get("username", "?")
        entities = m.get("entities") or {}
        sentiment_obj = entities.get("sentiment") or {}
        sentiment = sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None
        body = (m.get("body") or "").replace("\n", " ").strip()
        if len(body) > 280:
            body = body[:280] + "\u2026"
        if sentiment == "Bullish":
            bullish += 1
            tag = "Bullish"
        elif sentiment == "Bearish":
            bearish += 1
            tag = "Bearish"
        else:
            unlabeled += 1
            tag = "no-label"
        msg_list.append({"created_at": created, "user": user, "sentiment": tag, "body": body})

    total = bullish + bearish + unlabeled
    bull_pct = round(100 * bullish / total) if total else 0
    bear_pct = round(100 * bearish / total) if total else 0
    summary = (
        f"Bullish: {bullish} ({bull_pct}%) \u00b7 "
        f"Bearish: {bearish} ({bear_pct}%) \u00b7 "
        f"Unlabeled: {unlabeled} \u00b7 "
        f"Total: {total} most-recent messages"
    )
    return _ok({"source": "stocktwits", "symbol": ticker, "summary": summary, "bullish": bullish, "bearish": bearish, "messages": msg_list})


# ── Reddit ──

def _reddit_search_qs(ticker: str, limit: int) -> str:
    return urlencode({"q": ticker, "restrict_sr": "on", "sort": "new", "t": "week", "limit": limit})


def _strip_html_tags(content: str) -> str:
    if not content:
        return ""
    if "<!-- SC_OFF -->" in content and "<!-- SC_ON -->" in content:
        content = content.split("<!-- SC_OFF -->")[1].split("<!-- SC_ON -->")[0]
    text = re.sub(r"<[^>]+>", " ", content)
    return " ".join(html.unescape(text).split())


def _retry_after_seconds(exc: HTTPError) -> Optional[float]:
    try:
        val = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
        return min(float(val), 30.0) if val else None
    except (ValueError, TypeError, AttributeError):
        return None


def _fetch_subreddit_rss(ticker: str, sub: str, limit: int, timeout: float, _retry: bool = True) -> list[dict]:
    safe_sub = quote(sub, safe="")
    url = _REDDIT_RSS.format(sub=safe_sub, qs=_reddit_search_qs(ticker, limit))
    req = Request(url, headers={"User-Agent": _REDDIT_UA})
    try:
        with get_opener().open(req, timeout=timeout) as resp:
            root = ET.fromstring(resp.read())
    except HTTPError as exc:
        if exc.code == 429 and _retry:
            wait = _retry_after_seconds(exc) or 5.0
            logger.warning("Reddit RSS 429 for r/%s · %s — backing off %.1fs", sub, ticker, wait)
            time.sleep(wait)
            return _fetch_subreddit_rss(ticker, sub, limit, timeout, _retry=False)
        logger.warning("Reddit RSS fetch failed for r/%s · %s: %s", sub, ticker, exc)
        return []
    except (OSError, http.client.HTTPException, ET.ParseError) as exc:
        logger.warning("Reddit RSS fetch failed for r/%s · %s: %s", sub, ticker, exc)
        return []

    posts = []
    for entry in root.findall("atom:entry", _ATOM_NS)[:limit]:
        title_el = entry.find("atom:title", _ATOM_NS)
        published_el = entry.find("atom:published", _ATOM_NS)
        content_el = entry.find("atom:content", _ATOM_NS)
        title = (title_el.text if title_el is not None else "") or ""
        published = published_el.text if published_el is not None else None
        content_text = _strip_html_tags(content_el.text if content_el is not None else "")
        created_str = "?"
        if published:
            try:
                normalized = published[:-1] + "+00:00" if published.endswith("Z") else published
                from datetime import datetime
                created_str = datetime.fromisoformat(normalized).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
        posts.append({
            "title": title.replace("\n", " ").strip(),
            "created_str": created_str,
            "selftext": content_text[:240] + ("\u2026" if len(content_text) > 240 else ""),
            "source": "rss",
        })
    return posts


def _reddit(symbol: str, subreddits: Optional[str] = None, limit_per_sub: int = 5) -> str:
    ticker = symbol.strip().upper()
    if subreddits:
        subs = tuple(s.strip() for s in subreddits.split(",") if s.strip())
    else:
        subs = DEFAULT_SUBREDDITS

    timeout = 10.0
    inter_request_delay = 1.0
    blocks = []
    all_posts = []
    total_posts = 0

    for i, sub in enumerate(subs):
        if i > 0:
            time.sleep(inter_request_delay)
        sub_posts = _fetch_subreddit_rss(ticker, sub, limit_per_sub, timeout)
        total_posts += len(sub_posts)
        if not sub_posts:
            blocks.append(f"r/{sub}: <no posts found mentioning {ticker} in the past 7 days>")
            continue
        header = f"r/{sub} — {len(sub_posts)} recent posts mentioning {ticker} (via RSS feed):"
        lines = [header]
        for p in sub_posts:
            title = p.get("title", "")
            meta = p.get("created_str", "?")
            selftext = (p.get("selftext") or "").replace("\n", " ").strip()
            lines.append(f"  [{meta}] {title}" + (f"\n    body excerpt: {selftext}" if selftext else ""))
            all_posts.append({"subreddit": sub, "title": title, "date": meta, "selftext": selftext})
        blocks.append("\n".join(lines))

    if total_posts == 0:
        return _ok({
            "source": "reddit", "symbol": ticker,
            "summary": f"No Reddit posts found mentioning {ticker} across {', '.join(f'r/{s}' for s in subs)} in the past 7 days",
            "posts": [],
        })
    summary = "\n\n".join(blocks)
    return _ok({"source": "reddit", "symbol": ticker, "summary": summary, "posts": all_posts})


# ── Futu — SDK + HTTP API dual-path ──

def _to_futu_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if "." in s and s.split(".")[0] in ("US", "HK", "SZ", "SH", "SG", "JP"):
        return s
    if s.endswith(".HK"):
        return f"HK.{s[:-3].zfill(5)}"
    if s.endswith(".SZ"):
        return f"SZ.{s[:-3].zfill(6)}"
    if s.endswith(".SH"):
        return f"SH.{s[:-3].zfill(6)}"
    if s.isdigit():
        if len(s) == 6 and s[0] == "6":
            return f"SH.{s}"
        if len(s) == 6 and s[0] in ("0", "3"):
            return f"SZ.{s}"
        if len(s) == 5:
            return f"HK.{s}"
    return f"US.{s}"


def _futu_opend_reachable():
    import socket
    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False



def _futu_sdk_sentiment(symbol: str, limit: int = 30) -> Optional[str]:
    try:
        import futu
    except ImportError:
        logger.debug("Futu SDK not installed")
        return None
    if not _futu_opend_reachable():
        logger.debug("Futu OpenD not reachable, skipping SDK sentiment")
        return None
    futu_host = os.environ.get("FUTU_HOST", "127.0.0.1")
    futu_port = int(os.environ.get("FUTU_PORT", "11111"))
    futu_symbol = _to_futu_symbol(symbol)
    ctx = None
    try:
        ctx = futu.OpenQuoteContext(host=futu_host, port=futu_port)
        if not hasattr(ctx, "get_search_news"):
            return None
        ret, data = ctx.get_search_news(keyword=futu_symbol, max_count=limit)
        if ret != futu.RET_OK or data is None:
            return None
        if data is None or (hasattr(data, "empty") and data.empty):
            return None
        news_items = []
        for _, row in data.iterrows():
            news_items.append({
                "title": row.get("title", "No title"),
                "summary": row.get("summary", ""),
                "url": row.get("url", ""),
                "publish_time": str(row.get("publish_time", "")),
                "source": "futu_sdk",
            })
        if not news_items:
            return None
        news_str = ""
        for item in news_items:
            news_str += f"### {item['title']}\n"
            if item["summary"]:
                news_str += f"{item['summary']}\n"
            if item["url"]:
                news_str += f"Link: {item['url']}\n"
            if item["publish_time"]:
                news_str += f"Published: {item['publish_time']}\n"
            news_str += "\n"
        return f"## {symbol} News (Futu SDK):\n\n{news_str}"
    except Exception:
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def _futu_community(symbol: str, limit: int = 30) -> Optional[str]:
    ticker = symbol.strip().upper()
    params = urlencode({"keyword": ticker, "size": min(max(1, limit), 50)})
    url = f"{_FUTU_COMMUNITY_API}?{params}"
    req = Request(url, headers={"User-Agent": _FUTU_COMMUNITY_UA, "Accept": "application/json"})
    try:
        with get_opener().open(req, timeout=10.0) as resp:
            data = json.loads(resp.read())
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        logger.warning("Futu community fetch failed for %s: %s", ticker, exc)
        return None
    if not isinstance(data, dict) or data.get("code") != 0:
        logger.warning("Futu community API error for %s: %s", ticker, data)
        return None
    posts_data = data.get("data", [])
    if not posts_data:
        return None
    lines = []
    post_list = []
    for p in posts_data:
        title = (p.get("title") or "").strip()
        desc = p.get("desc") or ""
        desc = re.sub(r"<[^>]+>", "", desc)
        desc = html.unescape(desc).strip()
        pub_time = p.get("publish_time")
        url_link = p.get("url", "")
        if len(title) < 3 and len(desc) < 5:
            continue
        created_str = "?"
        if pub_time is not None:
            try:
                ts = int(pub_time)
                if ts > 1e12:
                    ts = ts // 1000
                from datetime import datetime
                created_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError, OSError):
                pass
        if len(desc) > 240:
            desc = desc[:240] + "\u2026"
        lines.append(f"[{created_str}] {title}")
        if desc:
            lines.append(f"  {desc}")
        if url_link:
            lines.append(f"  Link: {url_link}")
        lines.append("")
        post_list.append({"title": title, "desc": desc, "publish_time": created_str, "url": url_link})
    if not post_list:
        return None
    summary = "\n".join(lines)
    return f"## {ticker} Community Posts (Futu API):\n\n{summary}"


def _futu_sentiment(symbol: str, limit: int = 30) -> str:
    ticker = symbol.strip().upper()
    sdk_result = _futu_sdk_sentiment(ticker, limit=limit)
    api_result = _futu_community(ticker, limit=limit)
    sdk_available = sdk_result is not None
    api_available = api_result is not None
    if not sdk_available and not api_available:
        return _err("Futu sentiment unavailable: SDK unreachable and API failed")
    posts = []
    if sdk_result:
        posts.append({"type": "sdk_news", "content": sdk_result})
    if api_result:
        posts.append({"type": "community", "content": api_result})
    return _ok({"source": "futu", "symbol": ticker, "posts": posts, "sdk_available": sdk_available, "api_available": api_available})


# ── Combined entry-point ──

def _social_sentiment(symbol: str, source: str = "all", limit: int = 30) -> str:
    """Fetch social media sentiment from StockTwits, Reddit, and/or Futu."""
    source_lower = (source or "all").lower().strip()
    if source_lower == "stocktwits":
        return _stocktwits(symbol, limit=limit)
    elif source_lower == "reddit":
        return _reddit(symbol, limit_per_sub=max(1, limit // 3))
    elif source_lower == "futu":
        return _futu_sentiment(symbol, limit=limit)
    else:
        st_result = _stocktwits(symbol, limit=limit)
        rd_result = _reddit(symbol, limit_per_sub=max(1, limit // 3))
        futu_result = _futu_sentiment(symbol, limit=limit)
        st_data = json.loads(st_result)
        rd_data = json.loads(rd_result)
        futu_data = json.loads(futu_result)
        combined = {
            "symbol": symbol.strip().upper(),
            "sources": ["stocktwits", "reddit", "futu"],
            "stocktwits": st_data.get("data") if st_data.get("ok") else {"error": st_data.get("error")},
            "reddit": rd_data.get("data") if rd_data.get("ok") else {"error": rd_data.get("error")},
            "futu": futu_data.get("data") if futu_data.get("ok") else {"error": futu_data.get("error")},
        }
        return _ok(combined)


# ── MCP 注册 ──

def create_mcp_tools() -> list[MCPTool]:
    return [
        make_tool(
            name="social_sentiment",
            description="社交媒体情绪查询（StockTwits/Reddit/Futu 三源聚合）",
            handler=_social_sentiment,
            tags={"category:sentiment", "data-source:multi"},
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 AAPL, BABA"},
                    "source": {"type": "string", "description": "数据源: all/stocktwits/reddit/futu", "enum": ["all", "stocktwits", "reddit", "futu"]},
                    "limit": {"type": "integer", "description": "每个来源的最大条数，默认30"},
                },
                "required": ["symbol"],
            },
        ),
    ]
