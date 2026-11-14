import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.whatsapp import ai_service
from app.whatsapp.conversation import ConversationManager
from app.whatsapp.memory import history_tail
from app.whatsapp.session import session_manager
from tests.test_conversation import DummyRepo

PHONE = "2348012345678"
NOW = datetime(2026, 11, 14, 15, tzinfo=timezone(timedelta(hours=1)))


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(ai_service, "_ai_retry_at", 0)
    monkeypatch.setattr(ai_service, "nigeria_now", lambda: NOW)
    original_client = httpx.AsyncClient
    state = {"requests": [], "reply": {"intent": "SERVICE_BOOKING", "replyText": "Got it.", "sessionUpdates": {}}, "status": 200}
    def respond(request):
        state["requests"].append(json.loads(request.content))
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": {"message": "private failure details"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(state["reply"])}]}}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    return state


@pytest.fixture
def manager():
    repo = DummyRepo()
    repo.users[PHONE] = {"id": "customer-1", "name": "Gasper Samuel", "phone": PHONE,
                         "role": "customer", "profile": {"onboarded": True}}
    repo.find_matching_services = AsyncMock(return_value=[])
    return ConversationManager(repo)


async def draft(**extra):
    return await session_manager.update_session(PHONE, {"service": "Painting", "location": "Lugbe, Abuja",
        "date": "2026-11-18", "time": "10:00", "step": "AWAITING_CONFIRMATION", **extra})


@pytest.mark.asyncio
async def test_natural_correction_changes_friday_keeps_place_and_asks_exact_time(manager, model):
    await draft()
    model["reply"] = {"intent": "SERVICE_BOOKING", "replyText": "Friday afternoon, same place. What time works?",
                      "sessionUpdates": {"date": "2026-11-20", "location": "Ikeja"},
                      "timePeriod": "afternoon", "clearFields": ["time"]}
    result = await manager.handle_incoming_message(PHONE, "Actually, make it Friday afternoon, same place")
    saved = await session_manager.get_session(PHONE)
    assert saved["service"] == "Painting" and saved["location"] == "Lugbe, Abuja"
    assert saved["date"] == "2026-11-20" and saved["time"] is None
    assert "Friday" in result["text"] and "afternoon" in result["text"] and "What time" in result["text"]
    result = await manager.handle_incoming_message(PHONE, "2pm")
    assert "14:00" in result["text"] and "2026-11-20" in result["text"]
    assert len(model["requests"]) == 1  # Exact time replies remain immediate.


@pytest.mark.asyncio
async def test_faq_mid_booking_answers_without_mutating_draft(manager, model):
    await draft()
    model["reply"] = {"intent": "CONVERSATION_OR_FAQ", "replyText": "The listed provider sets the price. You can compare their quotes before booking.",
                      "sessionUpdates": {"service": "Electrical", "date": "2027-01-01"}}
    result = await manager.handle_incoming_message(PHONE, "How much will it cost?")
    saved = await session_manager.get_session(PHONE)
    assert "compare" in result["text"]
    assert saved["service"] == "Painting" and saved["date"] == "2026-11-18"
    assert saved["step"] == "AWAITING_CONFIRMATION"


@pytest.mark.asyncio
async def test_ai_still_runs_after_five_calls(model):
    result = await ai_service.analyze_intent("Could you explain how this works?", {"aiCalls": 100})
    assert result["source"] == "gemini" and len(model["requests"]) == 1
    assert model["requests"][0]["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.asyncio
async def test_recent_messages_are_passed_to_gemini(manager, model):
    await draft()
    await manager.handle_incoming_message(PHONE, "10am")
    model["reply"] = {"intent": "CONVERSATION_OR_FAQ", "replyText": "Your request is for Painting in Lugbe, Abuja.", "sessionUpdates": {}}
    await manager.handle_incoming_message(PHONE, "What did we agree on earlier?")
    prompt = model["requests"][-1]["contents"][0]["parts"][0]["text"]
    assert '"role": "user", "text": "10am"' in prompt
    assert '"role": "assistant"' in prompt and "Lugbe, Abuja" in prompt


@pytest.mark.asyncio
async def test_one_sentence_booking_from_main_menu(manager, model):
    await session_manager.update_session(PHONE, {"step": "MAIN_MENU"})
    model["reply"] = {"intent": "SERVICE_BOOKING", "replyText": "I have your request.", "sessionUpdates": {
        "service": "Painting", "location": "Lugbe, Abuja", "date": "2026-11-20", "time": "14:00"}}
    result = await manager.handle_incoming_message(PHONE, "I need a painter in Lugbe, Abuja next Friday at 2pm")
    assert "Painting" in result["text"] and "Lugbe, Abuja" in result["text"] and "14:00" in result["text"]
    assert (await session_manager.get_session(PHONE))["step"] == "AWAITING_CONFIRMATION"
    manager.repository.find_matching_services.assert_not_awaited()


@pytest.mark.asyncio
async def test_quota_failure_recovers_without_permanent_cutoff(model, monkeypatch, caplog):
    model["status"] = 429
    assert (await ai_service.analyze_intent("a plumber", {}))["source"] == "local"
    model["status"] = 200
    assert (await ai_service.analyze_intent("a plumber", {}))["fallbackReason"] == "temporary_cooldown"
    monkeypatch.setattr(ai_service, "_ai_retry_at", 0)
    assert (await ai_service.analyze_intent("a plumber", {}))["source"] == "gemini"
    assert len(model["requests"]) == 2
    assert "test-gemini-key" not in caplog.text and "private failure" not in caplog.text


@pytest.mark.asyncio
async def test_offline_correction_preserves_painting_in_same_place(manager, model):
    await draft()
    model["status"] = 503
    result = await manager.handle_incoming_message(PHONE, "Actually make it Friday afternoon, same place")
    saved = await session_manager.get_session(PHONE)
    assert saved["service"] == "Painting" and saved["location"] == "Lugbe, Abuja"
    assert saved["date"] == "2026-11-20" and saved["time"] is None
    assert "What time" in result["text"]


@pytest.mark.asyncio
async def test_model_cannot_write_wallet_role_or_arbitrary_steps(manager, model):
    await draft()
    model["reply"] = {"intent": "SERVICE_BOOKING", "replyText": "Got it.", "sessionUpdates": {
        "date": "2026-11-20", "step": "PAID", "wallet_balance": 999999, "role": "admin", "isConfirmed": True}}
    await manager.handle_incoming_message(PHONE, "Actually move it to Friday")
    saved = await session_manager.get_session(PHONE)
    assert saved["step"] == "AWAITING_CONFIRMATION" and "wallet_balance" not in saved and saved.get("role") != "admin"
    manager.repository.find_matching_services.assert_not_awaited()


def test_history_is_bounded_and_prompt_excludes_private_provider_data():
    assert len(history_tail([{"role": "user", "text": "hello"}] * 50)) == 12
    prompt = ai_service.build_prompt("Which provider is cheaper?", {"matchedProviders": [{
        "displayName": "Painter A", "priceLabel": "NGN 5000", "phone": "PRIVATE_PHONE", "raw": {"secret": "PRIVATE_PAYLOAD"}}]})
    assert "Painter A" in prompt and "NGN 5000" in prompt
    assert "PRIVATE_PHONE" not in prompt and "PRIVATE_PAYLOAD" not in prompt


def test_explicit_calendar_date_takes_precedence_over_weekday_label(model):
    assert ai_service.normalize_date("Friday 18 November 2026") == "2026-11-18"


@pytest.mark.asyncio
async def test_reset_discards_history(manager):
    await session_manager.update_session(PHONE, {"history": [{"role": "user", "text": "OLD_PRIVATE_CONTEXT"}]})
    await manager.handle_incoming_message(PHONE, "cancel")
    assert "OLD_PRIVATE_CONTEXT" not in json.dumps((await session_manager.get_session(PHONE)).get("history"))


@pytest.mark.asyncio
async def test_history_does_not_mix_concurrent_senders(manager, model):
    import asyncio
    other = "2348098765432"
    manager.repository.users[other] = {"id": "other", "name": "Ada", "phone": other, "role": "customer", "profile": {"onboarded": True}}
    await asyncio.gather(manager.handle_incoming_message(PHONE, "hello"), manager.handle_incoming_message(other, "hello"))
    assert "Gasper" in json.dumps((await session_manager.get_session(PHONE))["history"])
    assert "Ada" not in json.dumps((await session_manager.get_session(PHONE))["history"])
    assert "Gasper" not in json.dumps((await session_manager.get_session(other))["history"])


@pytest.mark.asyncio
async def test_first_request_is_remembered_while_collecting_name(model):
    repo = DummyRepo()
    manager = ConversationManager(repo)
    model["reply"] = {"intent": "SERVICE_BOOKING", "replyText": "Got it.", "sessionUpdates": {
        "service": "Painting", "location": "Lugbe, Abuja", "date": "2026-11-20", "time": "14:00"}}
    first = await manager.handle_incoming_message(PHONE, "Hi, I need a painter in Lugbe, Abuja next Friday at 2pm")
    assert "full name" in first["text"] and repo.users[PHONE]["name"] is None
    result = await manager.handle_incoming_message(PHONE, "Gasper Samuel")
    assert repo.users[PHONE]["name"] == "Gasper Samuel"
    assert "Painting" in result["text"] and "Lugbe, Abuja" in result["text"] and "14:00" in result["text"]


@pytest.mark.asyncio
async def test_short_uncertain_reply_gets_a_conversational_response(manager, model):
    await draft(step="AWAITING_DATETIME", date=None)
    model["reply"] = {"intent": "CONVERSATION_OR_FAQ", "replyText": "No rush. Do you have a preferred day, or would you like to compare providers first?", "sessionUpdates": {}}
    result = await manager.handle_incoming_message(PHONE, "not sure")
    assert len(model["requests"]) == 1 and "No rush" in result["text"]
    assert (await session_manager.get_session(PHONE))["time"] == "10:00"


@pytest.mark.asyncio
async def test_natural_confirmation_uses_backend_search(manager, model):
    await draft()
    await manager.handle_incoming_message(PHONE, "looks good")
    manager.repository.find_matching_services.assert_awaited_once_with(service="Painting", location="Lugbe, Abuja", limit=3)
    assert not model["requests"]
