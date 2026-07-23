"""MCP server configuration.

Lightweight config focused solely on server transport parameters —
no client/OAuth/live-broker concepts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

TransportType = Literal["stdio", "sse", "streamable-http"]
_VALID_TRANSPORTS: frozenset[str] = frozenset({"stdio", "sse", "streamable-http"})


@dataclass
class ServerConfig:
    """MCP server configuration.

    Attributes:
        name: Server name presented to MCP clients.
        transport: Transport protocol — ``"stdio"``, ``"sse"``, or
            ``"streamable-http"``.
        host: Bind address for HTTP-based transports.
        port: Listen port for HTTP-based transports.
    """

    name: str = "TradingTools"
    transport: TransportType = "stdio"
    host: str = "127.0.0.1"
    port: int = 8080

    def __post_init__(self) -> None:
        if self.transport not in _VALID_TRANSPORTS:
            raise ValueError(
                f"Invalid transport {self.transport!r}; "
                f"must be one of {sorted(_VALID_TRANSPORTS)}"
            )
        if not 1 <= self.port <= 65535:
            raise ValueError(f"Port must be 1-65535, got {self.port}")

    # ── Serialisation helpers ─────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict (JSON-safe)."""
        return {
            "name": self.name,
            "transport": self.transport,
            "host": self.host,
            "port": self.port,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerConfig:
        """Construct from a dict (e.g. parsed JSON)."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_json_file(cls, path: str | Path) -> ServerConfig:
        """Load config from a JSON file.

        Args:
            path: Path to a JSON config file.

        Returns:
            Parsed :class:`ServerConfig`.
        """
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        return cls.from_dict(raw)
