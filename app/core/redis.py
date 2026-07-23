"""Async Redis client accessor (lazy singleton).

The API publishes alarm/governance events to Redis; the Flask BFF fans them out
to browsers (FastAPI never opens a browser WebSocket). Publishing is best-effort
at call sites — a Redis outage must not fail a governance mutation.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.settings import get_settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        url = get_settings().redis_url or "redis://localhost:6379/0"
        _client = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
    return _client


async def dispose_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
