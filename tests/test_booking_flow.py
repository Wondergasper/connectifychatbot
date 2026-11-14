from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.whatsapp import ai_service, messages
from app.whatsapp.booking_details import schedule_updates
from app.whatsapp.conversation import ConversationManager
from app.whatsapp.session import session_manager
from tests.test_conversation import DummyRepo

PHONE = "2348012345678"
NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone(timedelta(hours=1)))


@pytest.fixture(autouse=True)
def nigeria_clock(monkeypatch):
    monkeypatch.setattr(ai_service, "nigeria_now", lambda: NOW)


@pytest.fixture
def manager():
    repo = DummyRepo()
    repo.users[PHONE] = {"id": "customer-1", "name": "Gasper Samuel", "phone": PHONE,
                         "role": "customer", "profile": {"onboarded": True}}
    repo.find_matching_services = AsyncMock(return_value=[])
    return ConversationManager(repo)


async def draft(**updates):
    return await session_manager.update_session(PHONE, {
        "service": "Painting", "location": "Lugbe, Abuja", "time": "10:00",
        "date": None, "step": "AWAITING_DATETIME", **updates,
    })


@pytest.mark.parametrize("text", ["18 November", "18th November", "18 Nov", "November 18", "November 18, 2026", "18 November 2026", "18th", "the 18th", "18"])
def test_normal_human_date_replies(text):
    assert ai_service.normalize_date(text, allow_day_only=True) == "2026-11-18"


@pytest.mark.parametrize("text", ["2026-02-31", "31 February 2026", "31/11/2026", "32nd", "0", "nonsense"])
def test_invalid_calendar_dates_are_rejected(text):
    assert ai_service.normalize_date(text, allow_day_only=True) is None


def test_year_and_month_rollover_are_shown_explicitly():
    assert ai_service.normalize_date("1 November") == "2027-11-01"
    assert ai_service.normalize_date("12th") == "2026-12-12"
    assert ai_service.normalize_date("29 February 2028") == "2028-02-29"
    assert ai_service.normalize_date("29 February 2027") is None


def test_bare_day_does_not_replace_captured_time():
    assert schedule_updates("18", {"time": "10:00", "date": None}) == {"date": "2026-11-18"}
    assert schedule_updates("18th", {"time": "10:00", "date": None}) == {"date": "2026-11-18"}
    assert schedule_updates("18 November", {"time": "10:00"}) == {"date": "2026-11-18"}
    assert schedule_updates("18", {"date": "2026-11-18", "time": None}) == {"time": "18:00"}
    assert schedule_updates("18", {}) == {}


@pytest.mark.asyncio
async def test_exact_reported_journey_preserves_details(manager, monkeypatch):
    no_ai = AsyncMock(side_effect=AssertionError("Booking replies must not use AI"))
    monkeypatch.setattr(ai_service, "analyze_intent", no_ai)
    result = await manager.handle_incoming_message(PHONE, {"actionId": "CATEGORY_PAINTING"})
    assert "area and city" in result["text"]
    assert "Where in Lagos" not in result["text"]
    result = await manager.handle_incoming_message(PHONE, "Lubge Abuja")
    assert "Did you mean *Lugbe, Abuja*" in result["text"]
    assert (await session_manager.get_session(PHONE))["location"] is None
    await manager.handle_incoming_message(PHONE, "yes")
    result = await manager.handle_incoming_message(PHONE, "10am")
    assert "Time saved: *10:00*" in result["text"]
    assert "Which date" in result["text"]
    result = await manager.handle_incoming_message(PHONE, "18 November")
    for detail in ["Painting", "Lugbe, Abuja", "2026-11-18", "10:00"]:
        assert detail in result["text"]
    assert (await session_manager.get_session(PHONE))["step"] == "AWAITING_CONFIRMATION"
    manager.repository.find_matching_services.assert_not_awaited()
    # The 26-minute pause must not restart onboarding or erase the booking.
    monkeypatch.setattr(ai_service, "nigeria_now", lambda: NOW + timedelta(minutes=26))
    result = await manager.handle_incoming_message(PHONE, "Hi")
    assert "2026-11-18" in result["text"] and "10:00" in result["text"]
    no_ai.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["AWAITING_DATETIME", "READY", "init", "ONBOARDING"])
async def test_partial_draft_recovers_even_if_step_was_overwritten(manager, step):
    await draft(step=step, aiCalls=5)
    result = await manager.handle_incoming_message(PHONE, "18th")
    assert "2026-11-18" in result["text"]
    assert "Painting" in result["text"] and "10:00" in result["text"]


@pytest.mark.asyncio
async def test_invalid_date_only_asks_for_date(manager):
    await draft()
    result = await manager.handle_incoming_message(PHONE, "31 February")
    assert "Which date" in result["text"] and "10:00" in result["text"]
    saved = await session_manager.get_session(PHONE)
    assert saved["time"] == "10:00" and saved["service"] == "Painting"


@pytest.mark.asyncio
async def test_missing_time_only_asks_for_time(manager):
    await draft(date="2026-11-18", time=None)
    result = await manager.handle_incoming_message(PHONE, "help")
    assert "What time" in result["text"] and "2026-11-18" in result["text"]


@pytest.mark.asyncio
async def test_change_date_keeps_other_details(manager):
    await draft(date="2026-11-18", step="AWAITING_CONFIRMATION")
    result = await manager.handle_incoming_message(PHONE, "no")
    assert "Which booking detail" in result["text"]
    await manager.handle_incoming_message(PHONE, {"actionId": "EDIT_DATE"})
    result = await manager.handle_incoming_message(PHONE, "19 November")
    assert "2026-11-19" in result["text"]
    assert "Painting" in result["text"] and "Lugbe, Abuja" in result["text"] and "10:00" in result["text"]


@pytest.mark.asyncio
async def test_change_service_keeps_schedule_and_location(manager):
    await draft(date="2026-11-18", step="AWAITING_CONFIRMATION")
    await manager.handle_incoming_message(PHONE, {"actionId": "EDIT_SERVICE"})
    result = await manager.handle_incoming_message(PHONE, {"actionId": "CATEGORY_ELECTRICAL"})
    assert "Electrical" in result["text"] and "2026-11-18" in result["text"] and "Lugbe, Abuja" in result["text"]


@pytest.mark.asyncio
async def test_unknown_confirmation_reply_does_not_clear_draft(manager):
    await draft(date="2026-11-18", step="AWAITING_CONFIRMATION")
    await manager.handle_incoming_message(PHONE, "hmm")
    saved = await session_manager.get_session(PHONE)
    assert saved["service"] == "Painting" and saved["date"] == "2026-11-18"


@pytest.mark.asyncio
async def test_no_coverage_preserves_draft_and_accepts_new_city(manager):
    await draft(date="2026-11-18", step="AWAITING_CONFIRMATION")
    result = await manager.handle_incoming_message(PHONE, "yes")
    manager.repository.find_matching_services.assert_awaited_once_with(service="Painting", location="Lugbe, Abuja", limit=3)
    assert "Lugbe, Abuja" in result["text"]
    result = await manager.handle_incoming_message(PHONE, "Ikeja, Lagos")
    assert "2026-11-18" in result["text"] and "10:00" in result["text"] and "Painting" in result["text"]


@pytest.mark.asyncio
async def test_past_date_and_time_are_not_confirmed(manager):
    await draft()
    result = await manager.handle_incoming_message(PHONE, "2026-11-13")
    assert "has passed" in result["text"]
    assert (await session_manager.get_session(PHONE))["step"] == "AWAITING_DATETIME"
    result = await manager.handle_incoming_message(PHONE, "today")
    assert "has passed" in result["text"]
    result = await manager.handle_incoming_message(PHONE, "tomorrow")
    assert "Please confirm" in result["text"]


@pytest.mark.asyncio
async def test_quota_limit_falls_back_without_resetting_context():
    response = await ai_service.analyze_intent("18 November", {"aiCalls": 5, "service": "Painting", "location": "Lugbe, Abuja", "time": "10:00"})
    assert not response["rateLimited"]
    assert response["sessionUpdates"]["date"] == "2026-11-18"


@pytest.mark.asyncio
async def test_greeting_after_name_does_not_repeat_name_question(manager):
    manager.repository.users[PHONE]["profile"] = {}
    await session_manager.update_session(PHONE, {"step": "ROLE_SELECTION"})
    result = await manager.handle_incoming_message(PHONE, "Hi!")
    assert "Gasper" in result["text"] and "How would you like" in result["text"]
    assert "full name" not in result["text"]


@pytest.mark.asyncio
async def test_completed_onboarding_stays_complete_on_next_greeting(manager):
    manager.repository.users[PHONE]["profile"] = {}
    await session_manager.update_session(PHONE, {"step": "CUSTOMER_SETUP_LOCATION"})
    await manager.handle_incoming_message(PHONE, "Lugbe, Abuja")
    assert manager.repository.users[PHONE]["profile"]["onboarded"] is True
    result = await manager.handle_incoming_message(PHONE, "hello")
    assert "How can Connectify help" in result["text"]


@pytest.mark.asyncio
async def test_menu_and_resume_preserve_booking(manager):
    await draft(date="2026-11-18", step="AWAITING_CONFIRMATION")
    await manager.handle_incoming_message(PHONE, "main menu")
    result = await manager.handle_incoming_message(PHONE, "continue")
    assert "Painting" in result["text"] and "2026-11-18" in result["text"] and "10:00" in result["text"]


@pytest.mark.asyncio
async def test_provider_setup_resumes_on_greeting(manager):
    manager.repository.users[PHONE]["role"] = "provider"
    await session_manager.update_session(PHONE, {"step": "PROVIDER_SETUP_PRICE", "providerCategory": "Painting"})
    result = await manager.handle_incoming_message(PHONE, "hi")
    assert "hourly rate" in result["text"]
    assert (await session_manager.get_session(PHONE))["step"] == "PROVIDER_SETUP_PRICE"
