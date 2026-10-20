import json
import logging
from typing import Dict, Any, Optional

from app.core.redis import get_redis_client

logger = logging.getLogger("connectify.session")

SESSION_TTL_SECONDS = 60 * 60 * 2  # 2 hours
KEY_PREFIX = "connectify:session:"

# In-memory dictionary fallback when Redis is absent
_memory_store: Dict[str, Dict[str, Any]] = {}


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
        key = f"{KEY_PREFIX}{phone_number}"
        client = await get_redis_client()

        if client is not None:
            try:
                raw = await client.get(key)
                if raw:
                    return json.loads(raw)
                fresh = default_session()
                await client.setex(key, SESSION_TTL_SECONDS, json.dumps(fresh))
                return fresh
            except Exception as e:
                logger.error(f"[SessionManager] Redis GET error for {phone_number}: {e}")

        # In-memory fallback
        if phone_number not in _memory_store:
            _memory_store[phone_number] = default_session()
        return dict(_memory_store[phone_number])

    async def update_session(self, phone_number: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        key = f"{KEY_PREFIX}{phone_number}"
        current = await self.get_session(phone_number)
        merged = {**current, **updates}

        client = await get_redis_client()
        if client is not None:
            try:
                await client.setex(key, SESSION_TTL_SECONDS, json.dumps(merged))
                return merged
            except Exception as e:
                logger.error(f"[SessionManager] Redis SET error for {phone_number}: {e}")

        _memory_store[phone_number] = merged
        return merged

    async def clear_session(self, phone_number: str) -> bool:
        key = f"{KEY_PREFIX}{phone_number}"
        client = await get_redis_client()
        if client is not None:
            try:
                await client.delete(key)
            except Exception as e:
                logger.error(f"[SessionManager] Redis DELETE error for {phone_number}: {e}")

        _memory_store.pop(phone_number, None)
        return True


session_manager = SessionManager()
