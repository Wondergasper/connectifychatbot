import pytest
from datetime import datetime, timedelta
from app.whatsapp.ai_service import normalize_date, normalize_time, validate_reply


def test_normalize_date():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")

    assert normalize_date("today") == today
    assert normalize_date("tomorrow") == tomorrow
    assert normalize_date("2026-12-25") == "2026-12-25"
    assert normalize_date("25-12-2026") == "2026-12-25"
    assert normalize_date("invalid-date") is None
    assert normalize_date(None) is None


def test_normalize_time():
    assert normalize_time("14:30") == "14:30"
    assert normalize_time("2pm") == "14:00"
    assert normalize_time("2:30 pm") == "14:30"
    assert normalize_time("9am") == "09:00"
    assert normalize_time("12am") == "00:00"
    assert normalize_time("12pm") == "12:00"
    assert normalize_time("25:00") is None
    assert normalize_time(None) is None


def test_validate_reply():
    parsed = {
        "replyText": "We found a great plumber for you!",
        "suggestions": ["Book Plumber Now", "Check Price Guide", "This title is much too long for WhatsApp Meta buttons", ""],
        "sessionUpdates": {
            "service": "Plumber",
            "location": "Lekki Phase 1",
            "date": "today",
            "time": "3pm",
            "urgency": "normal",
            "budget": "15000",
            "isConfirmed": True,
            "injected_fake_key": "should_be_ignored",
        },
    }

    reply, updates, suggestions = validate_reply(parsed)
    assert reply == "We found a great plumber for you!"
    assert updates["service"] == "Plumber"
    assert updates["location"] == "Lekki Phase 1"
    assert updates["time"] == "15:00"
    assert updates["budget"] == "15000"
    assert updates["isConfirmed"] is True
    assert "injected_fake_key" not in updates
    assert len(suggestions) <= 3
    assert all(len(s) <= 20 for s in suggestions)
    assert "Book Plumber Now" in suggestions


def test_extract_intent_heuristically():
    from app.whatsapp.ai_service import extract_intent_heuristically

    # Case 1: Service and location in query
    res = extract_intent_heuristically("I need a plumber in Ikeja tomorrow 10am", {})
    assert res["sessionUpdates"]["service"] == "Plumbing"
    assert res["sessionUpdates"]["location"] == "Ikeja"
    assert res["sessionUpdates"]["isConfirmed"] is True
    assert "Plumbing" in res["replyText"]
    assert "Ikeja" in res["replyText"]
    assert "trouble understanding" not in res["replyText"]
    assert len(res["suggestions"]) > 0

    # Case 2: Only service mentioned
    res2 = extract_intent_heuristically("generator repair", {})
    assert res2["sessionUpdates"]["service"] == "Generator Repair"
    assert res2["sessionUpdates"]["isConfirmed"] is False
    assert "area and city" in res2["replyText"]
    assert "trouble understanding" not in res2["replyText"]

    # Case 3: Empty / generic search query
    res3 = extract_intent_heuristically("find a service", {})
    assert res3["sessionUpdates"]["isConfirmed"] is False
    assert "trouble understanding" not in res3["replyText"]
    assert "browse verified categories" in res3["replyText"]

    # Case 4: FAQ Escrow & Payment
    res4 = extract_intent_heuristically("how do i pay? is it safe or scam?", {})
    assert "Escrow" in res4["replyText"]
    assert "100% protected" in res4["replyText"]
    assert res4["sessionUpdates"]["isConfirmed"] is False
    assert len(res4["suggestions"]) > 0

    # Case 5: Diagnostic - Generator smoking
    res5 = extract_intent_heuristically("my gen is smoking white smoke", {})
    assert "Generator" in res5["replyText"]
    assert "turn off" in res5["replyText"].lower()
    assert res5["sessionUpdates"]["service"] == "Generator Repair"
    assert "Book Generator Pro" in res5["suggestions"]

    # Case 6: Diagnostic - AC water leak
    res6 = extract_intent_heuristically("my ac is leaking water inside", {})
    assert "AC" in res6["replyText"]
    assert res6["sessionUpdates"]["service"] == "AC & Refrigeration"


@pytest.mark.asyncio
async def test_analyze_intent_fallback_without_error():
    from app.whatsapp.ai_service import analyze_intent

    # Even with an invalid model or network failure, analyze_intent must never return an error message
    res = await analyze_intent("electrician in Lekki", {})
    assert res["rateLimited"] is False
    assert res["sessionUpdates"]["service"].lower() in ["electrician", "electrical"]
    assert res["sessionUpdates"]["location"].lower() == "lekki"
    assert "trouble understanding" not in res["replyText"]
    assert isinstance(res.get("suggestions"), list)
