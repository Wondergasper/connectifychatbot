import asyncio
import logging
import time
from typing import Optional, Dict, Any
from app.core.config import settings

logger = logging.getLogger("connectify.redis")

_redis_client: Optional[Any] = None
_redis_available: bool = False
_memory_store: Dict[str, str] = {}
_retry_at = 0.0
_connect_lock = asyncio.Lock()
RECONNECT_COOLDOWN_SECONDS = 30


async def mark_redis_unavailable():
    """Open the circuit once; session operations must not each retry a dead host."""
    global _redis_client, _redis_available, _retry_at
    client, _redis_client = _redis_client, None
    _redis_available = False
    _retry_at = time.monotonic() + RECONNECT_COOLDOWN_SECONDS
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            pass


async def get_redis_client():
    global _redis_client, _redis_available, _retry_at
    if _redis_client is not None and _redis_available:
        return _redis_client

    if not settings.REDIS_URL or time.monotonic() < _retry_at:
        return None
    import redis.asyncio as aioredis
    from redis.asyncio.retry import Retry
    from redis.backoff import NoBackoff

    async with _connect_lock:
        if _redis_client is not None and _redis_available:
            return _redis_client
        if time.monotonic() < _retry_at:
            return None
        client = None
        try:
            client = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=1,
                socket_connect_timeout=1,
                retry=Retry(NoBackoff(), 0),
            )
            # Also bound DNS/TLS/pool waits, not just socket operations.
            await asyncio.wait_for(client.ping(), timeout=1.5)
            _redis_client = client
            _redis_available = True
            _retry_at = 0
            logger.info("Connected to Redis successfully.")
            return _redis_client
        except Exception:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
            await mark_redis_unavailable()
            logger.warning("Redis unavailable; using memory sessions and pausing connection retries for 30 seconds.")
            return None


async def redis_get(key: str) -> Optional[str]:
    client = await get_redis_client()
    if client:
        try:
            return await client.get(key)
        except Exception as e:
            logger.warning(f"Redis get failed: {e}")
            await mark_redis_unavailable()
    return _memory_store.get(key)


async def redis_set(key: str, value: str, ex: int = 7200) -> None:
    client = await get_redis_client()
    if client:
        try:
            await client.set(key, value, ex=ex)
            return
        except Exception as e:
            logger.warning(f"Redis set failed: {e}")
            await mark_redis_unavailable()
    _memory_store[key] = value


async def redis_delete(key: str) -> None:
    client = await get_redis_client()
    if client:
        try:
            await client.delete(key)
            return
        except Exception as e:
            logger.warning(f"Redis delete failed: {e}")
            await mark_redis_unavailable()
    _memory_store.pop(key, None)


async def close_redis_client() -> None:
    global _redis_client, _redis_available, _retry_at
    if _redis_client is not None:
        try:
            await _redis_client.close()
        except Exception as e:
            logger.warning(f"Error closing Redis client: {e}")
        finally:
            _redis_client = None
            _redis_available = False
    _retry_at = 0

