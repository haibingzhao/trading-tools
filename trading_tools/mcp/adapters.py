"""Adapter helpers — thin utilities for wrapping functions as MCPTool handlers.

Each module under ``trading_tools.tools`` uses :func:`make_tool` to create
:class:`~trading_tools.mcp.registry.MCPTool` instances with minimal boilerplate.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from trading_tools.mcp.registry import MCPTool


def make_tool(
    name: str,
    description: str,
    handler: Callable[..., Any],
    *,
    tags: set[str] | None = None,
    parameters: dict[str, Any] | None = None,
) -> MCPTool:
    """Create an :class:`MCPTool` from a callable.

    If *parameters* is not supplied, a basic JSON-Schema is inferred from the
    handler's function signature (type hints and defaults).

    Args:
        name: Unique tool name (snake_case).
        description: Human-readable description for MCP clients.
        handler: The callable implementing the tool.
        tags: Optional set of tags for grouping.
        parameters: Explicit JSON-Schema for the tool's inputs.
            When ``None``, a schema is auto-generated from the handler signature.
    """
    if parameters is None:
        parameters = _schema_from_callable(handler)
    return MCPTool(
        name=name,
        description=description,
        handler=handler,
        parameters=parameters,
        tags=frozenset(tags) if tags else frozenset(),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PYTHON_TO_JSON_TYPE: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _schema_from_callable(fn: Callable[..., Any]) -> dict[str, Any]:
    """Generate a minimal JSON-Schema from *fn*'s signature.

    Uses ``typing.get_type_hints()`` to resolve stringified annotations
    produced by ``from __future__ import annotations`` (PEP 563).
    """
    sig = inspect.signature(fn)

    # Resolve stringified type hints (PEP 563)
    try:
        from typing import get_type_hints
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        prop: dict[str, Any] = {}

        # Prefer resolved type hint; fall back to raw annotation
        annotation = hints.get(param_name, param.annotation)
        if annotation is not inspect.Parameter.empty:
            json_type = _resolve_json_type(annotation)
            if json_type:
                prop["type"] = json_type

        # Default value
        if param.default is inspect.Parameter.empty:
            # No default → required (unless it's Optional-like)
            if annotation is inspect.Parameter.empty or not _is_optional(annotation):
                required.append(param_name)
        else:
            prop["default"] = param.default

        properties[param_name] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _resolve_json_type(annotation: Any) -> Optional[str]:
    """Map a Python type annotation to a JSON-Schema type string."""
    # Handle Optional[X] (Union[X, None])
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        import typing
        if origin is typing.Union:
            args = [a for a in annotation.__args__ if a is not type(None)]
            if len(args) == 1:
                return _resolve_json_type(args[0])
            return None
        # list, dict, etc.
        if origin is list:
            return "array"
        if origin is dict:
            return "object"
        return None

    return _PYTHON_TO_JSON_TYPE.get(annotation)


def _is_optional(annotation: Any) -> bool:
    """Check if *annotation* is Optional[X] (i.e. Union[X, None])."""
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        import typing
        if origin is typing.Union:
            return type(None) in annotation.__args__
    return False
