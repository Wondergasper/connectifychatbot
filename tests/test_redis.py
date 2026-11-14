import asyncio
from unittest.mock import AsyncMock

import pytest
import redis.asyncio

from app.core import redis as store
from app.core.config import settings


@pytest.fixture(autouse=True)
def reset_connection(monkeypatch):
    monkeypatch.setattr(store, "_redis_client", None)
    monkeypatch.setattr(store, "_redis_available", False)
    monkeypatch.setattr(store, "_retry_at", 0)
    monkeypatch.setattr(store, "_connect_lock", asyncio.Lock())
    monkeypatch.setattr(settings, "REDIS_URL", "redis://unreachable.invalid:6379")


@pytest.mark.asyncio
async def test_dead_redis_is_attempted_once_during_many_session_operations(monkeypatch):
    client = AsyncMock()
    client.ping.side_effect = ConnectionError("offline")
    calls = []
    def create(url, **options):
        calls.append((url, options))
        return client
    monkeypatch.setattr(redis.asyncio, "from_url", create)
    results = await asyncio.gather(*(store.get_redis_client() for _ in range(20)))
    assert results == [None] * 20
    assert len(calls) == 1
    assert calls[0][1]["retry"].get_retries() == 0
    assert calls[0][1]["socket_connect_timeout"] == 1
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconnect_after_cooldown_recovers_without_restart(monkeypatch):
    failed, healthy = AsyncMock(), AsyncMock()
    failed.ping.side_effect = ConnectionError("offline")
    clients = iter([failed, healthy])
    monkeypatch.setattr(redis.asyncio, "from_url", lambda *args, **kw: next(clients))
    assert await store.get_redis_client() is None
    monkeypatch.setattr(store, "_retry_at", 0)
    assert await store.get_redis_client() is healthy
    assert await store.get_redis_client() is healthy
    assert healthy.ping.await_count == 1


@pytest.mark.asyncio
async def test_empty_redis_url_disables_connection_attempts(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    def unexpected(*args, **kwargs):
        raise AssertionError("Blank Redis URL must not try localhost")
    monkeypatch.setattr(redis.asyncio, "from_url", unexpected)
    assert await store.get_redis_client() is None


@pytest.mark.asyncio
async def test_failed_session_read_opens_circuit(monkeypatch):
    client = AsyncMock()
    client.get.side_effect = ConnectionError("offline")
    monkeypatch.setattr(store, "_redis_client", client)
    monkeypatch.setattr(store, "_redis_available", True)
    assert await store.redis_get("missing") is None
    assert await store.get_redis_client() is None
    assert not store._redis_available
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_unresponsive_redis_cannot_hold_first_reply_indefinitely(monkeypatch):
    client = AsyncMock()
    never_ready = asyncio.Event()
    client.ping.side_effect = never_ready.wait
    monkeypatch.setattr(redis.asyncio, "from_url", lambda *args, **kwargs: client)
    assert await asyncio.wait_for(store.get_redis_client(), timeout=3) is None
    client.aclose.assert_awaited_once()
