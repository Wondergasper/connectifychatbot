import json
import logging
import time
from typing import Dict, Any, Optional

from app.core.redis import get_redis_client, mark_redis_unavailable

logger = logging.getLogger("connectify.session")

SESSION_TTL_SECONDS = 60 * 60 * 24  # Keep an unfinished booking across an overnight pause.
KEY_PREFIX = "connectify:session:"

# In-memory dictionary fallback when Redis is absent
_memory_store: Dict[str, Dict[str, Any]] = {}
_memory_expires: Dict[str, float] = {}
_dirty_sessions = set()


def cache_session(phone_number, value):
    _memory_store[phone_number] = json.loads(json.dumps(value))
    _memory_expires[phone_number] = time.monotonic() + SESSION_TTL_SECONDS


def cached_session(phone_number):
    if _memory_expires.get(phone_number, float("inf")) <= time.monotonic():
        _memory_store.pop(phone_number, None)
        _memory_expires.pop(phone_number, None)
        _dirty_sessions.discard(phone_number)
    value = _memory_store.get(phone_number)
    return json.loads(json.dumps(value)) if value is not None else None


def session_phone(phone_number: str) -> str:
    """Use one session identity for database E.164 numbers and webhook senders."""
    return str(phone_number).strip().lstrip("+")


def default_session() -> Dict[str, Any]:
    return {
        "step": "init",
        "service": None,
        "location": None,
        "date": None,
        "time": None,
        "isConfirmed": False,
        "matchedProviders": [],
        "aiCalls": 0,
    }


class SessionManager:
    async def get_session(self, phone_number: str) -> Dict[str, Any]:
        phone_number = session_phone(phone_number)
        key = f"{KEY_PREFIX}{phone_number}"
        cached = cached_session(phone_number)
        client = await get_redis_client()

        if client is not None:
            try:
                if phone_number in _dirty_sessions and cached is not None:
                    await client.setex(key, SESSION_TTL_SECONDS, json.dumps(cached))
                    _dirty_sessions.discard(phone_number)
                    return cached
                raw = await client.get(key)
                if raw:
                    restored = json.loads(raw)
                    cache_session(phone_number, restored)
                    return restored
                # Rehydrate Redis after an outage instead of discarding a draft
                # that continued in this process while Redis was unavailable.
                fresh = cached or default_session()
                await client.setex(key, SESSION_TTL_SECONDS, json.dumps(fresh))
                cache_session(phone_number, fresh)
                return json.loads(json.dumps(fresh))
            except Exception as e:
                logger.error(f"[SessionManager] Redis GET error for {phone_number}: {e}")
                await mark_redis_unavailable()

        # In-memory fallback
        if phone_number not in _memory_store:
            cache_session(phone_number, default_session())
        return json.loads(json.dumps(_memory_store[phone_number]))

    async def update_session(self, phone_number: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        phone_number = session_phone(phone_number)
        key = f"{KEY_PREFIX}{phone_number}"
        current = await self.get_session(phone_number)
        merged = {**current, **updates}
        cache_session(phone_number, merged)
        _dirty_sessions.add(phone_number)

        client = await get_redis_client()
        if client is not None:
            try:
                await client.setex(key, SESSION_TTL_SECONDS, json.dumps(merged))
                _dirty_sessions.discard(phone_number)
                return merged
            except Exception as e:
                logger.error(f"[SessionManager] Redis SET error for {phone_number}: {e}")
                await mark_redis_unavailable()

        return merged

    async def clear_session(self, phone_number: str) -> bool:
        phone_number = session_phone(phone_number)
        key = f"{KEY_PREFIX}{phone_number}"
        # Keep a tombstone so reconnecting cannot resurrect a cancelled draft.
        cache_session(phone_number, default_session())
        _dirty_sessions.add(phone_number)
        client = await get_redis_client()
        if client is not None:
            try:
                await client.delete(key)
                _dirty_sessions.discard(phone_number)
            except Exception as e:
                logger.error(f"[SessionManager] Redis DELETE error for {phone_number}: {e}")
                await mark_redis_unavailable()

        return True


session_manager = SessionManager()
