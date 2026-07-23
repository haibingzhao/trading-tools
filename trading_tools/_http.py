"""Shared HTTP helpers: per-host throttling + JSON GET."""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Default User-Agent.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_JITTER_MAX_S = 0.4


def positive_env_float(name: str, default: float) -> float:
    """Read a positive float env var, warning and falling back on invalid values."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("invalid %s=%r, using default %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("non-positive %s=%r, using default %s", name, raw, default)
        return default
    return value


class HostThrottle:
    """Process-wide minimum-spacing gate keyed by an arbitrary host bucket."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, bucket: str, min_interval: float) -> None:
        if min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            last = self._last.get(bucket)
            if last is None or now >= last + min_interval:
                fire_at = now
            else:
                fire_at = last + min_interval + random.uniform(0.0, _JITTER_MAX_S)
            self._last[bucket] = fire_at
        sleep_for = fire_at - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)


_THROTTLE = HostThrottle()

_SESSIONS: dict[str, requests.Session] = {}
_SESSIONS_LOCK = threading.Lock()


def _session_for(bucket: str) -> requests.Session:
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(bucket)
        if session is None:
            session = requests.Session()
            _SESSIONS[bucket] = session
        return session


def resolve_min_interval(env_name: str, default: float) -> float:
    """Resolve a per-provider minimum request interval from the environment."""
    return positive_env_float(env_name, default)


def throttled_get(
    url: str,
    *,
    host_key: str,
    min_interval: float,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> requests.Response:
    """GET *url* after waiting out the per-host minimum interval."""
    merged_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        merged_headers.update(headers)
    _THROTTLE.wait(host_key, min_interval)
    session = _session_for(host_key)
    return session.get(url, params=params, headers=merged_headers, timeout=timeout)


def throttled_get_json(
    url: str,
    *,
    host_key: str,
    min_interval: float,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> Any:
    """Throttled GET that decodes the response body as JSON."""
    response = throttled_get(
        url,
        host_key=host_key,
        min_interval=min_interval,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()
