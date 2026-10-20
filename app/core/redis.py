import logging
from typing import Optional, Dict, Any
from app.core.config import settings

logger = logging.getLogger("connectify.redis")

_redis_client: Optional[Any] = None
_redis_available: bool = False
_memory_store: Dict[str, str] = {}


async def get_redis_client():
    global _redis_client, _redis_available
    if _redis_client is not None and _redis_available:
        return _redis_client

    import redis.asyncio as aioredis

    urls_to_try = []
    if settings.REDIS_URL:
        urls_to_try.append(settings.REDIS_URL)
    if "127.0.0.1" not in settings.REDIS_URL and "localhost" not in settings.REDIS_URL:
        urls_to_try.append("redis://127.0.0.1:6379")

    for url in urls_to_try:
        try:
            client = aioredis.from_url(
                url,
                decode_responses=True,
                socket_timeout=3,
                socket_connect_timeout=3
            )
            await client.ping()
            _redis_client = client
            _redis_available = True
            logger.info(f"Connected to Redis successfully ({url.split('@')[-1]}).")
            return _redis_client
        except Exception as e:
            logger.debug(f"Redis connect failed for {url}: {e}")

    logger.info("Redis unavailable, using high-performance in-memory session store.")
    _redis_available = False
    return None


async def redis_get(key: str) -> Optional[str]:
    client = await get_redis_client()
    if client:
        try:
            return await client.get(key)
        except Exception as e:
            logger.warning(f"Redis get failed: {e}")
    return _memory_store.get(key)


async def redis_set(key: str, value: str, ex: int = 7200) -> None:
    client = await get_redis_client()
    if client:
        try:
            await client.set(key, value, ex=ex)
            return
        except Exception as e:
            logger.warning(f"Redis set failed: {e}")
    _memory_store[key] = value


async def redis_delete(key: str) -> None:
    client = await get_redis_client()
    if client:
        try:
            await client.delete(key)
            return
        except Exception as e:
            logger.warning(f"Redis delete failed: {e}")
    _memory_store.pop(key, None)


async def close_redis_client() -> None:
    global _redis_client, _redis_available
    if _redis_client is not None:
        try:
            await _redis_client.close()
        except Exception as e:
            logger.warning(f"Error closing Redis client: {e}")
        finally:
            _redis_client = None
            _redis_available = False

