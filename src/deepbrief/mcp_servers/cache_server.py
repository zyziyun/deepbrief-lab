"""cache_server — a remote-style MCP server that caches web fetch results.

Why this server exists: when several research agents are working in parallel,
they often want the same URL fetched (Wikipedia, an FAQ, etc.). A shared cache
prevents duplicate fetches.

Why it's HTTP-only: this models the *remote* MCP pattern. In production you
wouldn't subprocess-spawn a cache server — multiple agents on different
machines would point at one shared service.

Run it:
    python -m deepbrief.mcp_servers.cache_server         # http on :8765
    PORT=9000 python -m deepbrief.mcp_servers.cache_server

Inspect it:
    npx @modelcontextprotocol/inspector
    # then connect manually to http://localhost:8765/mcp

Tools exposed:
    - cache_get(key)         → {hit: bool, value: any}
    - cache_set(key, value)  → {ok: True}
    - cache_clear()          → {cleared: int}
    - cache_stats()          → {size: int, hits: int, misses: int}

"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "deepbrief-cache",
    instructions="In-memory cache for deduplicating web fetches across research agents.",
)

# Module-level state. In production you'd back this with Redis.
_CACHE: dict[str, Any] = {}
_STATS = {"hits": 0, "misses": 0}


@mcp.tool()
def cache_get(key: str) -> dict:
    """Retrieve a value from the cache.

    Args:
        key: Lookup key (typically a URL or content hash).

    Returns:
        Dict with `hit` (bool) and `value` (the cached data, or None on miss).
    """
    if key in _CACHE:
        _STATS["hits"] += 1
        return {"hit": True, "value": _CACHE[key]}
    _STATS["misses"] += 1
    return {"hit": False, "value": None}


@mcp.tool()
def cache_set(key: str, value: str) -> dict:
    """Store a value in the cache.

    Args:
        key: Lookup key (typically a URL or content hash).
        value: Value to store. Strings only — agent results should be
            JSON-serialized before storing.

    Returns:
        Dict with `ok: True`.
    """
    _CACHE[key] = value
    return {"ok": True, "size": len(_CACHE)}


@mcp.tool()
def cache_clear() -> dict:
    """Clear all cached entries. Use sparingly — it invalidates other agents' work."""
    n = len(_CACHE)
    _CACHE.clear()
    _STATS["hits"] = 0
    _STATS["misses"] = 0
    return {"cleared": n}


@mcp.tool()
def cache_stats() -> dict:
    """Return cache size and hit/miss counters since startup."""
    total = _STATS["hits"] + _STATS["misses"]
    rate = _STATS["hits"] / total if total else 0.0
    return {"size": len(_CACHE), "hits": _STATS["hits"], "misses": _STATS["misses"], "hit_rate": round(rate, 3)}


def main() -> None:
    port = int(os.environ.get("PORT", "8765"))
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
