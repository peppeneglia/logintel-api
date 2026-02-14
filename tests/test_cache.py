"""Tests for Redis cache layer (Block 3)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from app.services.cache import (
    cache_get,
    cache_set,
    close_redis,
    get_redis,
    init_redis,
    set_redis,
)


# ─── Fixtures ───────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def fake_redis():
    """Provide a fakeredis instance and wire it into the cache module."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(client)
    yield client
    set_redis(None)
    await client.aclose()


# ─── Init / Close ───────────────────────────────────────────────────────

class TestInitClose:
    def test_init_redis_with_empty_url(self):
        init_redis("")
        assert get_redis() is None

    def test_init_redis_with_url(self):
        init_redis("redis://localhost:6379")
        assert get_redis() is not None
        # Cleanup
        set_redis(None)

    @pytest.mark.asyncio
    async def test_close_redis(self):
        init_redis("redis://localhost:6379")
        await close_redis()
        assert get_redis() is None

    @pytest.mark.asyncio
    async def test_close_redis_when_none(self):
        set_redis(None)
        await close_redis()  # should not raise
        assert get_redis() is None


# ─── cache_get / cache_set ──────────────────────────────────────────────

class TestCacheOperations:
    @pytest.mark.asyncio
    async def test_set_and_get(self, fake_redis):
        data = {"distance": 500.0, "duration": 18000}
        await cache_set("route:abc", data, ttl=3600)
        result = await cache_get("route:abc")
        assert result == data

    @pytest.mark.asyncio
    async def test_get_miss(self, fake_redis):
        result = await cache_get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_list_value(self, fake_redis):
        data = [1, 2, 3]
        await cache_set("list:key", data, ttl=60)
        result = await cache_get("list:key")
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_ttl_is_set(self, fake_redis):
        await cache_set("ttl:test", {"v": 1}, ttl=7200)
        ttl = await fake_redis.ttl("ttl:test")
        assert ttl > 0
        assert ttl <= 7200

    @pytest.mark.asyncio
    async def test_set_overwrites(self, fake_redis):
        await cache_set("key", {"v": 1}, ttl=60)
        await cache_set("key", {"v": 2}, ttl=60)
        result = await cache_get("key")
        assert result == {"v": 2}


# ─── Disabled cache (no URL) ───────────────────────────────────────────

class TestCacheDisabled:
    @pytest.mark.asyncio
    async def test_get_returns_none_when_disabled(self):
        set_redis(None)
        result = await cache_get("any:key")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_does_nothing_when_disabled(self):
        set_redis(None)
        await cache_set("any:key", {"v": 1}, ttl=60)  # should not raise


# ─── Graceful degradation (Redis errors) ───────────────────────────────

class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_get_returns_none_on_connection_error(self, fake_redis):
        fake_redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))
        result = await cache_get("route:abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_does_not_raise_on_connection_error(self, fake_redis):
        fake_redis.setex = AsyncMock(side_effect=ConnectionError("Redis down"))
        await cache_set("route:abc", {"v": 1}, ttl=60)  # should not raise
