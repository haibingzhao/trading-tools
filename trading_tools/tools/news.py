"""news tools — 公司新闻 + 全球新闻 + MCP 注册."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from trading_tools._common import ok, err, get_opener, coerce_list
from trading_tools.mcp.adapters import make_tool
from trading_tools.mcp.registry import MCPTool

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_FINNHUB_BASE = "https://finnhub.io/api/v1"


# ── Helpers ──

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _http_get_json(url: str, headers: dict | None = None) -> Any:
    """GET request → parsed JSON.  Raises on failure."""
    hdrs = {"User-Agent": "trading-tools/1.0", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with get_opener().open(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


# ── Finnhub ──

def _finnhub_key() -> Optional[str]:
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    return key or None


def _finnhub_sentiment(symbol: str) -> Dict[str, Any]:
    api_key = _finnhub_key()
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY not configured")
    params = urllib.parse.urlencode({"symbol": symbol, "token": api_key})
    url = f"{_FINNHUB_BASE}/news-sentiment?{params}"
    payload = _http_get_json(url)
    buzz = payload.get("buzz") or {}
    sentiment = payload.get("sentiment") or {}
    return {
        "source": "finnhub", "symbol": symbol,
        "buzz": {
            "buzz": buzz.get("buzz"),
            "articles_in_last_week": buzz.get("articlesInLastWeek"),
            "buzz_score": buzz.get("buzzScore"),
        },
        "sentiment": {
            "bullish_percent": sentiment.get("bullishPercent"),
            "bearish_percent": sentiment.get("bearishPercent"),
        },
        "raw": payload,
    }


def _finnhub_news(symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    api_key = _finnhub_key()
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY not configured")
    fd = from_date or _days_ago(7)
    td = to_date or _today()
    params = urllib.parse.urlencode({"symbol": symbol, "from": fd, "to": td, "token": api_key})
    url = f"{_FINNHUB_BASE}/company-news?{params}"
    payload = _http_get_json(url)
    if not isinstance(payload, list):
        raise RuntimeError(f"Finnhub returned unexpected payload type: {type(payload).__name__}")
    articles: List[Dict[str, Any]] = []
    for item in payload[:limit]:
        ts = item.get("datetime")
        pub_date = None
        if ts:
            try:
                pub_date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError, TypeError):
                pub_date = None
        articles.append({"title": item.get("headline", ""), "summary": item.get("summary", ""), "source": item.get("source", ""), "url": item.get("url", ""), "datetime": ts, "published": pub_date})
    return articles


# ── Alpha Vantage ──

_AV_BASE = "https://www.alphavantage.co/query"


def _av_key() -> Optional[str]:
    key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    return key or None


def _alphavantage_sentiment(symbol: str, limit: int = 20) -> Dict[str, Any]:
    api_key = _av_key()
    if not api_key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY not configured")
    params = urllib.parse.urlencode({"function": "NEWS_SENTIMENT", "tickers": symbol, "apikey": api_key, "limit": limit})
    url = f"{_AV_BASE}?{params}"
    payload = _http_get_json(url)
    if "Error Message" in payload:
        raise RuntimeError(f"Alpha Vantage error: {payload['Error Message']}")
    if "Note" in payload:
        raise RuntimeError(f"Alpha Vantage rate limit: {payload['Note']}")
    feed = payload.get("feed") or []
    articles: List[Dict[str, Any]] = []
    for item in feed[:limit]:
        ticker_sentiments = item.get("ticker_sentiment") or []
        sym_score = None
        relevance = None
        for ts in ticker_sentiments:
            if ts.get("ticker", "").upper() == symbol.upper():
                try:
                    sym_score = float(ts.get("ticker_sentiment_score"))
                except (TypeError, ValueError):
                    sym_score = None
                try:
                    relevance = float(ts.get("relevance_score"))
                except (TypeError, ValueError):
                    relevance = None
                break
        articles.append({
            "title": item.get("title", ""), "summary": item.get("summary", ""),
            "source": item.get("source", ""), "url": item.get("url", ""),
            "published": item.get("time_published", ""),
            "sentiment_score": sym_score, "relevance": relevance,
            "overall_sentiment": {"score": item.get("overall_sentiment_score"), "label": item.get("overall_sentiment_label")},
        })
    scores = [a["sentiment_score"] for a in articles if a["sentiment_score"] is not None]
    avg_score = sum(scores) / len(scores) if scores else None
    return {"source": "alpha_vantage", "symbol": symbol, "count": len(articles), "avg_sentiment_score": round(avg_score, 4) if avg_score is not None else None, "articles": articles}


def _alphavantage_news(symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
    result = _alphavantage_sentiment(symbol, limit=limit)
    return result.get("articles", [])


# ── Futu SDK + HTTP fallback ──

_FUTU_NEWS_API = "https://ai-news-search.futunn.com/news_search"


def _futu_opend_reachable():
    import socket
    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


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


def _futu_sdk_news(symbol: str, limit: int = 10, from_date: Optional[str] = None, to_date: Optional[str] = None, news_sub_type: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    try:
        import futu  # type: ignore
    except ImportError:
        return None
    if not _futu_opend_reachable():
        return None
    host = os.environ.get("FUTU_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_PORT", "11111"))
    futu_symbol = _to_futu_symbol(symbol)
    ctx = None
    try:
        ctx = futu.OpenQuoteContext(host=host, port=port)
        if not hasattr(ctx, "get_search_news"):
            return None
        news_kwargs: Dict[str, Any] = {"keyword": futu_symbol, "max_count": limit}
        if news_sub_type is not None:
            try:
                from futu import NewsSubType as _NewsSubType
                _sub_type_map = {"ALL": _NewsSubType.ALL, "NEWS": _NewsSubType.NEWS, "NOTICE": _NewsSubType.NOTICE, "RATING": _NewsSubType.RATING}
                _mapped = _sub_type_map.get(news_sub_type.upper())
                if _mapped is not None:
                    news_kwargs["news_sub_type"] = _mapped
            except ImportError:
                pass
        ret, data = ctx.get_search_news(**news_kwargs)
        if ret != futu.RET_OK or data is None:
            return None
        if hasattr(data, "empty") and data.empty:
            return []
        if hasattr(data, "iterrows"):
            rows = [row for _, row in data.iterrows()]
        elif isinstance(data, list):
            rows = data
        else:
            return []
        start_dt = None
        end_dt = None
        if from_date:
            try:
                start_dt = datetime.strptime(from_date, "%Y-%m-%d")
            except ValueError:
                pass
        if to_date:
            try:
                end_dt = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                pass
        articles: List[Dict[str, Any]] = []
        for row in rows[:limit]:
            title = row.get("title", "") if hasattr(row, "get") else getattr(row, "title", "")
            link = row.get("url", "") if hasattr(row, "get") else getattr(row, "url", "")
            pub_time = str(row.get("publish_time", "")) if hasattr(row, "get") else str(getattr(row, "publish_time", ""))
            if start_dt or end_dt:
                try:
                    pdt = datetime.strptime(pub_time[:19], "%Y-%m-%d %H:%M:%S") if pub_time else None
                except (ValueError, TypeError):
                    pdt = None
                if pdt:
                    if start_dt and pdt < start_dt:
                        continue
                    if end_dt and pdt > end_dt:
                        continue
            articles.append({
                "title": title,
                "news_sub_type": row.get("news_sub_type", "") if hasattr(row, "get") else getattr(row, "news_sub_type", ""),
                "source": row.get("source", "futu") if hasattr(row, "get") else getattr(row, "source", "futu"),
                "url": link, "published": pub_time,
                "view_count": row.get("view_count", 0) if hasattr(row, "get") else getattr(row, "view_count", 0),
                "related_securities": row.get("related_securities", "") if hasattr(row, "get") else getattr(row, "related_securities", ""),
            })
        return articles
    except Exception:
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def _futu_http_news(symbol: str, limit: int = 10, news_type: str = "1") -> Optional[List[Dict[str, Any]]]:
    try:
        params = urllib.parse.urlencode({"keyword": symbol, "size": min(limit, 50), "news_type": news_type, "sort_type": 2, "lang": "zh-CN"})
        url = f"{_FUTU_NEWS_API}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "futu-news-search/0.0.2 (easy-trading)"})
        with get_opener().open(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("code") != 0:
            return None
        raw_items = payload.get("data") or []
        articles: List[Dict[str, Any]] = []
        for item in raw_items[:limit]:
            pub_time = item.get("publish_time", "")
            if pub_time:
                try:
                    dt = datetime.fromisoformat(str(pub_time).replace("Z", "+00:00"))
                    pub_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, AttributeError):
                    pub_time = str(pub_time)
            articles.append({"title": item.get("title", ""), "summary": "", "source": "futu", "url": item.get("url", ""), "published": pub_time})
        return articles
    except Exception:
        return None


def _futu_news(symbol: str, limit: int = 10, from_date: Optional[str] = None, to_date: Optional[str] = None, news_sub_type: Optional[str] = None) -> str:
    articles = _futu_sdk_news(symbol, limit=limit, from_date=from_date, to_date=to_date, news_sub_type=news_sub_type)
    if articles is None:
        articles = _futu_http_news(symbol, limit=limit)
        if articles and (from_date or to_date):
            start_dt = end_dt = None
            if from_date:
                try:
                    start_dt = datetime.strptime(from_date, "%Y-%m-%d")
                except ValueError:
                    pass
            if to_date:
                try:
                    end_dt = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
                except ValueError:
                    pass
            if start_dt or end_dt:
                filtered: List[Dict[str, Any]] = []
                for a in articles:
                    pt = a.get("published", "")
                    if not pt:
                        continue
                    try:
                        pdt = datetime.strptime(str(pt)[:19], "%Y-%m-%d %H:%M:%S")
                    except (ValueError, TypeError):
                        continue
                    if start_dt and pdt < start_dt:
                        continue
                    if end_dt and pdt > end_dt:
                        continue
                    filtered.append(a)
                articles = filtered
    if articles is None:
        return err("Futu news unavailable")
    return ok({"source": "futu", "symbol": symbol, "count": len(articles), "articles": articles})


# ── Yahoo Finance fallback ──

_YAHOO_SEARCH_BASE = "https://query2.finance.yahoo.com/v1/finance/search"


def _yahoo_news(symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
    params = urllib.parse.urlencode({"q": symbol, "quotes_count": 0, "news_count": limit, "enable_fuzzy_query": "true"})
    url = f"{_YAHOO_SEARCH_BASE}?{params}"
    payload = _http_get_json(url)
    raw_news = payload.get("news") or []
    articles: List[Dict[str, Any]] = []
    for item in raw_news[:limit]:
        if "content" in item and isinstance(item["content"], dict):
            content = item["content"]
            title = content.get("title", "")
            summary = content.get("summary", "")
            provider = content.get("provider") or {}
            publisher = provider.get("displayName", "")
            url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
            link = url_obj.get("url", "")
            pub_date_str = content.get("pubDate", "")
            pub_date = None
            if pub_date_str:
                try:
                    dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                    pub_date = dt.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, AttributeError):
                    pub_date = None
        else:
            title = item.get("title", "")
            summary = item.get("summary", "")
            publisher = item.get("publisher", "")
            link = item.get("link", "")
            pub_ts = item.get("providerPublishTime")
            pub_date = None
            if pub_ts:
                try:
                    pub_date = datetime.fromtimestamp(int(pub_ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError, TypeError):
                    pub_date = None
        articles.append({"title": title, "summary": summary, "source": publisher, "url": link, "published": pub_date})
    return articles



# ── Global news ──

def _global_news(query: str, limit: int = 10, from_date: Optional[str] = None, to_date: Optional[str] = None) -> str:
    """Fetch global/macro news with multi-source fallback."""
    errors: List[str] = []
    seen_titles: set = set()
    all_articles: List[Dict[str, Any]] = []

    def _dedup_add(articles: List[Dict[str, Any]]) -> None:
        for a in articles:
            t = (a.get("title") or "").strip()
            if t and t not in seen_titles:
                seen_titles.add(t)
                all_articles.append(a)

    # 1. Futu SDK global news (try index symbols)
    try:
        import futu as _futu  # type: ignore
        if not _futu_opend_reachable():
            raise RuntimeError("Futu OpenD not reachable")
        host = os.environ.get("FUTU_HOST", "127.0.0.1")
        port = int(os.environ.get("FUTU_PORT", "11111"))
        ctx = None
        try:
            ctx = _futu.OpenQuoteContext(host=host, port=port)
            if hasattr(ctx, "get_search_news"):
                for market_code in ("US.SPX", "US.DJI", "US.IXIC"):
                    try:
                        ret, data = ctx.get_search_news(keyword=market_code, max_count=limit)
                        if ret == _futu.RET_OK and data is not None:
                            if hasattr(data, "empty") and data.empty:
                                continue
                            rows = [row for _, row in data.iterrows()] if hasattr(data, "iterrows") else (data if isinstance(data, list) else [])
                            sdk_articles: List[Dict[str, Any]] = []
                            for row in rows:
                                title = row.get("title", "") if hasattr(row, "get") else getattr(row, "title", "")
                                link = row.get("url", "") if hasattr(row, "get") else getattr(row, "url", "")
                                pub_time = str(row.get("publish_time", "")) if hasattr(row, "get") else str(getattr(row, "publish_time", ""))
                                sdk_articles.append({
                                    "title": title,
                                    "news_sub_type": row.get("news_sub_type", "") if hasattr(row, "get") else getattr(row, "news_sub_type", ""),
                                    "source": row.get("source", "futu") if hasattr(row, "get") else getattr(row, "source", "futu"),
                                    "url": link, "published": pub_time,
                                    "view_count": row.get("view_count", 0) if hasattr(row, "get") else getattr(row, "view_count", 0),
                                })
                            _dedup_add(sdk_articles)
                            if all_articles:
                                break
                    except Exception:
                        continue
        finally:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass
    except ImportError:
        pass
    except Exception as exc:
        errors.append(f"[Futu SDK global] {exc}")

    # 2. Futu HTTP API
    if len(all_articles) < limit:
        http_articles = _futu_http_news(query, limit=limit, news_type="1")
        if http_articles:
            _dedup_add(http_articles)

    # 3. Finnhub general news
    if len(all_articles) < limit and _finnhub_key():
        try:
            api_key = _finnhub_key()
            params = urllib.parse.urlencode({"category": "general", "token": api_key})
            url = f"{_FINNHUB_BASE}/news?{params}"
            payload = _http_get_json(url)
            if isinstance(payload, list):
                fh_articles: List[Dict[str, Any]] = []
                for item in payload[:limit]:
                    ts = item.get("datetime")
                    pub_date = None
                    if ts:
                        try:
                            pub_date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                        except (ValueError, OSError, TypeError):
                            pub_date = None
                    fh_articles.append({"title": item.get("headline", ""), "summary": item.get("summary", ""), "source": item.get("source", ""), "url": item.get("url", ""), "published": pub_date})
                _dedup_add(fh_articles)
        except Exception as exc:
            errors.append(f"[Finnhub global] {exc}")

    # 4. Yahoo Finance search
    if len(all_articles) < limit:
        try:
            yf_articles = _yahoo_news(query, limit=limit)
            _dedup_add(yf_articles)
        except Exception as exc:
            errors.append(f"[Yahoo Finance global] {exc}")

    # 5. Alpha Vantage
    if len(all_articles) < limit and _av_key():
        try:
            av_articles = _alphavantage_news(query, limit=limit)
            _dedup_add(av_articles)
        except Exception as exc:
            errors.append(f"[Alpha Vantage global] {exc}")

    # Client-side date filter
    if from_date or to_date:
        filtered: List[Dict[str, Any]] = []
        for a in all_articles:
            pub = a.get("published", "")
            if not pub:
                continue
            pub_str = str(pub)[:10]
            if from_date and pub_str < from_date:
                continue
            if to_date and pub_str > to_date:
                continue
            filtered.append(a)
        all_articles = filtered

    all_articles = all_articles[:limit]
    if not all_articles and errors:
        return err("All data sources failed: " + "; ".join(errors))
    return ok({"query": query, "count": len(all_articles), "articles": all_articles})


# ── Unified entry points ──

def _news_sentiment(symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None, source: str = "auto", limit: int = 20) -> str:
    """Fetch news with sentiment scores for a stock ticker."""
    symbol = symbol.strip().upper()
    errors: List[str] = []

    if source in ("auto", "finnhub"):
        try:
            sentiment = _finnhub_sentiment(symbol)
            try:
                news = _finnhub_news(symbol, from_date=from_date, to_date=to_date, limit=limit)
                sentiment["recent_news"] = news
            except Exception:
                sentiment["recent_news"] = []
            return ok(sentiment)
        except Exception as exc:
            msg = f"[Finnhub] {exc}"
            errors.append(msg)
            if source == "finnhub":
                return err(msg)

    if source in ("auto", "futu"):
        articles = _futu_sdk_news(symbol, limit=limit, from_date=from_date, to_date=to_date)
        if articles is None:
            articles = _futu_http_news(symbol, limit=limit)
        if articles is not None:
            return ok({"source": "futu", "symbol": symbol, "count": len(articles), "avg_sentiment_score": None, "note": "Futu does not provide sentiment scores.", "articles": articles})
        elif source == "futu":
            return err("Futu news unavailable")

    if source in ("auto", "alpha_vantage"):
        try:
            result = _alphavantage_sentiment(symbol, limit=limit)
            return ok(result)
        except Exception as exc:
            msg = f"[Alpha Vantage] {exc}"
            errors.append(msg)
            if source == "alpha_vantage":
                return err(msg)

    if source == "auto":
        try:
            articles = _yahoo_news(symbol, limit=limit)
            return ok({"source": "yahoo_finance", "symbol": symbol, "count": len(articles), "avg_sentiment_score": None, "note": "Yahoo Finance does not provide sentiment scores.", "articles": articles})
        except Exception as exc:
            errors.append(f"[Yahoo Finance] {exc}")

    return err("All data sources failed: " + "; ".join(errors))


def _company_news(symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None, limit: int = 20, source: str = "auto") -> str:
    """Fetch company-specific news articles."""
    symbol = symbol.strip().upper()
    errors: List[str] = []

    if source == "futu":
        return _futu_news(symbol, limit=limit, from_date=from_date, to_date=to_date)

    if source in ("auto", "finnhub"):
        try:
            articles = _finnhub_news(symbol, from_date=from_date, to_date=to_date, limit=limit)
            if articles:
                return ok({"source": "finnhub", "symbol": symbol, "count": len(articles), "articles": articles})
        except Exception as exc:
            msg = f"[Finnhub] {exc}"
            errors.append(msg)
            if source == "finnhub":
                return err(msg)

    if source == "auto":
        futu_articles = _futu_sdk_news(symbol, limit=limit, from_date=from_date, to_date=to_date)
        if futu_articles is None:
            futu_articles = _futu_http_news(symbol, limit=limit)
        if futu_articles:
            return ok({"source": "futu", "symbol": symbol, "count": len(futu_articles), "articles": futu_articles})

    if source in ("auto", "alpha_vantage"):
        try:
            articles = _alphavantage_news(symbol, limit=limit)
            if articles:
                return ok({"source": "alpha_vantage", "symbol": symbol, "count": len(articles), "articles": articles})
        except Exception as exc:
            msg = f"[Alpha Vantage] {exc}"
            errors.append(msg)
            if source == "alpha_vantage":
                return err(msg)

    if source == "auto":
        try:
            articles = _yahoo_news(symbol, limit=limit)
            return ok({"source": "yahoo_finance", "symbol": symbol, "count": len(articles), "articles": articles})
        except Exception as exc:
            errors.append(f"[Yahoo Finance] {exc}")

    return err("All data sources failed: " + "; ".join(errors))


# ── MCP 注册 ──

def create_mcp_tools() -> list[MCPTool]:
    return [
        make_tool(
            name="company_news",
            description="公司新闻查询（Finnhub/Futu/Alpha Vantage/Yahoo 多源 fallback）",
            handler=_company_news,
            tags={"category:news", "data-source:multi"},
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 AAPL, 09988.HK, 600519.SH"},
                    "from_date": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                    "to_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                    "limit": {"type": "integer", "description": "返回条数，默认20"},
                    "source": {"type": "string", "description": "数据源: auto/finnhub/futu/alpha_vantage", "enum": ["auto", "finnhub", "futu", "alpha_vantage"]},
                },
                "required": ["symbol"],
            },
        ),
        make_tool(
            name="global_news",
            description="全球/宏观新闻查询（支持关键词搜索，多源 fallback）",
            handler=_global_news,
            tags={"category:news", "data-source:multi"},
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，如 'Fed rate', 'AI chip'"},
                    "from_date": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                    "to_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                    "limit": {"type": "integer", "description": "返回条数，默认10"},
                },
                "required": ["query"],
            },
        ),
    ]
