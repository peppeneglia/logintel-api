"""
Redis cache layer (Upstash) — FRD Section 4.5.

Provides async get/set with JSON serialization and graceful degradation.
If Redis is unavailable or unconfigured, all operations silently return None.

Budget: ~14 commands/prediction worst case (10k/day free tier → ~700 predictions/day).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


def init_redis(url: str) -> None:
    """Initialise the shared async Redis client from an Upstash Redis URL."""
    global _redis
    if not url:
        logger.warning("UPSTASH_REDIS_URL not set — cache disabled")
        return
    _redis = aioredis.from_url(url, decode_responses=True)
    logger.info("Redis cache initialised")


async def close_redis() -> None:
    """Close the Redis connection pool."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Redis cache closed")


async def cache_get(key: str) -> dict | list | None:
    """GET key from Redis, JSON-deserialise and return. None on miss or error."""
    if _redis is None:
        return None
    try:
        raw = await _redis.get(key)
        if raw is None:
            from app.metrics import metrics_collector
            metrics_collector.record_cache_miss()
            return None
        from app.metrics import metrics_collector
        metrics_collector.record_cache_hit()
        return json.loads(raw)
    except Exception:
        logger.warning("Redis GET failed for key=%s", key, exc_info=True)
        from app.metrics import metrics_collector
        metrics_collector.record_cache_miss()
        return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    """JSON-serialise value and SETEX into Redis. Silently ignores errors."""
    if _redis is None:
        return
    try:
        await _redis.setex(key, ttl, json.dumps(value))
    except Exception:
        logger.warning("Redis SET failed for key=%s", key, exc_info=True)


def get_redis() -> aioredis.Redis | None:
    """Return the current Redis client (for testing)."""
    return _redis


def set_redis(client: aioredis.Redis | None) -> None:
    """Override the Redis client (for testing)."""
    global _redis
    _redis = client
