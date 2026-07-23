"""Shared utilities for trading_tools modules.

- JSON envelope helpers (ok/err)
- Proxy-aware HTTP opener (build_opener/get_opener)
- HTTP GET + JSON parsing (http_get_json)
- MCP parameter coercion (coerce_list)
- URL redaction for logging (redact_url)
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# JSON envelope helpers
# ---------------------------------------------------------------------------

def ok(data: Any) -> str:
    """Return a JSON success envelope string."""
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def err(msg: str) -> str:
    """Return a JSON error envelope string."""
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Proxy-aware HTTP opener
# ---------------------------------------------------------------------------

def build_opener():
    """Build a URL opener that respects HTTP_PROXY / HTTPS_PROXY / BY_PROXY env vars.

    BY_PROXY is a whitelist: only listed domains route through the proxy.
    """
    import ssl as _ssl
    handlers: list = []
    proxies: dict = {}
    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    by_proxy_raw = os.environ.get("BY_PROXY", "")
    if proxies and by_proxy_raw:
        by_proxy_patterns = [p.strip().lower() for p in by_proxy_raw.split(",") if p.strip()]

        class _WhitelistProxyHandler(urllib.request.ProxyHandler):
            def proxy_open(self, req, proxy, proxy_type):
                host = req.host.split(":")[0].lower()
                if any(host == p.lstrip("*") or host.endswith(p.lstrip("*"))
                       for p in by_proxy_patterns if p.startswith("*")):
                    return super().proxy_open(req, proxy, proxy_type)
                if host in by_proxy_patterns:
                    return super().proxy_open(req, proxy, proxy_type)
                return None

        handlers.append(_WhitelistProxyHandler(proxies))
        # Strip NO_PROXY / no_proxy so urllib's default handler is a no-op
        for v in ("NO_PROXY", "no_proxy"):
            os.environ.pop(v, None)
    elif proxies:
        handlers.append(urllib.request.ProxyHandler(proxies))
    # Handle SSL certificate verification (common on macOS Python installs)
    try:
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    except Exception:
        pass
    return urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()


_opener = None


def get_opener():
    """Return a cached proxy-aware URL opener (singleton)."""
    global _opener
    if _opener is None:
        _opener = build_opener()
    return _opener


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_TIMEOUT = 30


def http_get_json(url: str, *, timeout: int = _TIMEOUT) -> Any:
    """Issue a GET request and parse the response as JSON."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with get_opener().open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# MCP parameter coercion
# ---------------------------------------------------------------------------

def coerce_list(value: Any) -> list | None:
    """Coerce a JSON-encoded string to a list (MCP client compat).

    Handles the following input shapes:
    - None → None (return all sections)
    - list → as-is
    - JSON string of a list (e.g. '["key_stats"]') → parsed list
    - JSON string of an empty dict (e.g. '{}') → None (treat as no filter)
    - JSON string of a dict (e.g. '{"key_stats": true}') → list of keys
    - plain string (e.g. 'key_stats') → [value]
    """
    if value is None:
        return None
    if isinstance(value, list):
        return value if value else None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return [value]  # single string as one-element list
        if isinstance(parsed, list):
            return parsed if parsed else None
        if isinstance(parsed, dict):
            return list(parsed.keys()) if parsed else None
        # parsed to a scalar (int, bool, etc.) – wrap as single-element list
        return [parsed]
    if isinstance(value, dict):
        return list(value.keys()) if value else None
    return value


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def redact_url(url: str) -> str:
    """Mask API keys/tokens in a URL for safe logging."""
    return re.sub(r'(token|apikey|key)=([^&]+)', r'\1=***', url)
