import json
from unittest.mock import AsyncMock

import pytest

from app.whatsapp import session as sessions


@pytest.mark.asyncio
async def test_redis_outage_keeps_last_booking_and_rehydrates_recovery(monkeypatch):
    phone = "2348012345678"
    initial = {"step": "AWAITING_DATETIME", "service": "Painting", "location": "Lugbe, Abuja", "time": "10:00", "date": None}
    client = AsyncMock()
    client.get.return_value = json.dumps(initial)
    monkeypatch.setattr(sessions, "get_redis_client", AsyncMock(return_value=client))
    assert (await sessions.session_manager.get_session(phone))["service"] == "Painting"
    monkeypatch.setattr(sessions, "get_redis_client", AsyncMock(return_value=None))
    await sessions.session_manager.update_session(phone, {"date": "2026-11-18"})
    # Redis still has the old pre-outage value. Local progress wins on reconnect.
    monkeypatch.setattr(sessions, "get_redis_client", AsyncMock(return_value=client))
    restored = await sessions.session_manager.get_session(phone)
    assert restored["date"] == "2026-11-18" and restored["time"] == "10:00"
    assert json.loads(client.setex.await_args.args[2])["date"] == "2026-11-18"


@pytest.mark.asyncio
async def test_cancel_during_outage_does_not_resurrect_old_draft(monkeypatch):
    phone = "2348012345678"
    monkeypatch.setattr(sessions, "get_redis_client", AsyncMock(return_value=None))
    await sessions.session_manager.update_session(phone, {"service": "Painting"})
    await sessions.session_manager.clear_session(phone)
    client = AsyncMock()
    client.get.return_value = json.dumps({"service": "Painting"})
    monkeypatch.setattr(sessions, "get_redis_client", AsyncMock(return_value=client))
    assert (await sessions.session_manager.get_session(phone))["service"] is None


@pytest.mark.asyncio
async def test_new_process_restores_redis_draft_after_pause(monkeypatch):
    client = AsyncMock()
    client.get.return_value = json.dumps({"step": "AWAITING_DATETIME", "service": "Painting", "location": "Abuja", "time": "10:00"})
    monkeypatch.setattr(sessions, "get_redis_client", AsyncMock(return_value=client))
    fresh_manager = sessions.SessionManager()
    assert (await fresh_manager.get_session("2348012345678"))["location"] == "Abuja"
    assert sessions.SESSION_TTL_SECONDS == 86400
