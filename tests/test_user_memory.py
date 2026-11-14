import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.repositories.connectify_repository import ConnectifyRepository
from app.whatsapp.conversation import ConversationManager
from app.whatsapp import ai_service
from app.whatsapp.session import session_manager
from app.whatsapp.user_memory import clean_memory, get_memory, record_usage, prompt_memory
from tests.test_conversation import DummyRepo

PHONE = "2348012345678"
DETAILS = {"service": "Painting", "location": "Lugbe, Abuja", "date": "2026-11-18", "time": "10:00"}


class MemoryDatabase:
    """JSONB compare-and-swap behavior shared by fresh repository instances."""
    def __init__(self):
        self.users = {"user-1": {"id": "user-1", "profile": {"onboarded": True, "savedLocation": "Abuja", "otherAppSetting": "keep"}}}
        self.conflict_once = False

    def from_(self, table):
        assert table == "app_users"
        return MemoryQuery(self)


class MemoryQuery:
    def __init__(self, db):
        self.db, self.user_id, self.payload, self.expected = db, None, None, None

    def select(self, fields):
        return self

    def eq(self, key, value):
        assert key == "id"
        self.user_id = value
        return self

    def single(self):
        return self

    def update(self, payload):
        self.payload = copy.deepcopy(payload)
        return self

    def filter(self, column, operator, value):
        assert (column, operator) == ("profile", "eq")
        self.expected = json.loads(value)
        return self

    def is_(self, column, value):
        assert (column, value) == ("profile", "null")
        self.expected = None
        return self

    def execute(self):
        row = self.db.users[self.user_id]
        if self.payload is None:
            return SimpleNamespace(data=copy.deepcopy(row))
        if self.db.conflict_once:
            self.db.conflict_once = False
            row["profile"]["concurrentEdit"] = "preserve this too"
            return SimpleNamespace(data=[])
        if row.get("profile") != self.expected:
            return SimpleNamespace(data=[])
        row.update(copy.deepcopy(self.payload))
        return SimpleNamespace(data=[copy.deepcopy(row)])


@pytest.fixture
def manager():
    repo = DummyRepo()
    repo.users[PHONE] = {"id": "user-1", "name": "Gasper Samuel", "phone": PHONE, "role": "customer", "profile": {"onboarded": True}}
    repo.find_matching_services = AsyncMock(return_value=[])
    return ConversationManager(repo)


@pytest.mark.asyncio
async def test_usage_survives_fresh_repository_and_session_reset(manager):
    db = MemoryDatabase()
    repo = ConnectifyRepository(client=db)
    await repo.update_usage_memory({"id": "user-1"}, details=DETAILS)
    # A newly constructed repository reads the durable profile, not a session cache.
    second = ConnectifyRepository(client=db)
    user = {"id": "user-1", "profile": {}}
    await second.update_usage_memory(user, enabled=True)
    assert get_memory(user)["lastRequest"]["location"] == "Lugbe, Abuja"
    manager.repository.users[PHONE]["profile"] = copy.deepcopy(db.users["user-1"]["profile"])
    await session_manager.clear_session(PHONE)
    result = await ConversationManager(manager.repository).handle_incoming_message(PHONE, "hi")
    assert "Last time you looked for" in result["text"] and "Painting" in result["text"]


@pytest.mark.asyncio
async def test_profile_compare_and_swap_preserves_concurrent_edits():
    db = MemoryDatabase()
    db.conflict_once = True
    await ConnectifyRepository(client=db).update_usage_memory({"id": "user-1"}, details=DETAILS)
    profile = db.users["user-1"]["profile"]
    assert profile["concurrentEdit"] == "preserve this too"
    assert profile["otherAppSetting"] == "keep" and profile["savedLocation"] == "Abuja"
    assert profile["assistantMemory"]["lastRequest"]["service"] == "Painting"


def test_unconfirmed_requests_do_not_count_as_bookings_and_replays_are_deduplicated():
    memory = record_usage({}, DETAILS)
    assert memory["serviceCounts"] == {} and "lastBooking" not in memory
    booking = {"id": "booking-1", "date": "2026-11-18", "start_time": "10:00", "provider": {"name": "Painter A"}}
    memory = record_usage(memory, DETAILS, booking)
    memory = record_usage(memory, DETAILS, booking)
    assert memory["serviceCounts"] == {"Painting": 1}
    assert memory["lastBooking"]["provider"] == "Painter A"


@pytest.mark.asyncio
async def test_draft_edits_are_not_learned_until_confirmation(manager):
    await manager.handle_incoming_message(PHONE, {"actionId": "CATEGORY_PAINTING"})
    await manager.handle_incoming_message(PHONE, "Lugbe, Abuja")
    assert "assistantMemory" not in manager.repository.users[PHONE]["profile"]
    await session_manager.update_session(PHONE, {"date": "2099-11-18", "time": "10:00", "step": "AWAITING_CONFIRMATION"})
    await manager.handle_incoming_message(PHONE, "yes")
    memory = get_memory(manager.repository.users[PHONE])
    assert memory["lastRequest"]["service"] == "Painting"
    assert "date" not in memory["lastRequest"]


@pytest.mark.asyncio
async def test_reuse_remembers_only_service_and_area_not_old_date_or_time(manager):
    await manager.repository.update_usage_memory(manager.repository.users[PHONE], details=DETAILS, booking={"id": "past", "date": "2020-01-01", "start_time": "10:00"})
    result = await manager.handle_incoming_message(PHONE, "same as last time")
    saved = await session_manager.get_session(PHONE)
    assert saved["service"] == "Painting" and saved["location"] == "Lugbe, Abuja"
    assert saved["date"] is None and saved["time"] is None
    assert "Which date and time" in result["text"]


@pytest.mark.asyncio
async def test_memory_controls_persist_without_erasing_other_profile_fields(manager):
    user = manager.repository.users[PHONE]
    user["profile"]["savedLocation"] = "Home area"
    await manager.repository.update_usage_memory(user, details=DETAILS)
    result = await manager.handle_incoming_message(PHONE, "memory")
    assert "Painting" in result["text"] and "Lugbe, Abuja" in result["text"]
    await manager.handle_incoming_message(PHONE, "memory off")
    await manager.repository.update_usage_memory(user, details={"service": "Electrical", "location": "Lagos"})
    assert get_memory(user)["lastRequest"]["service"] == "Painting"
    assert (await session_manager.get_session(PHONE))["usageMemory"] == {}
    result = await manager.handle_incoming_message(PHONE, "same again")
    assert "don't have a saved" in result["text"]
    await manager.handle_incoming_message(PHONE, "forget my preferences")
    assert get_memory(user)["recentServices"] == [] and not get_memory(user)["enabled"]
    assert user["profile"]["savedLocation"] == "Home area" and user["profile"]["onboarded"]
    await manager.handle_incoming_message(PHONE, "memory on")
    assert get_memory(user)["enabled"]


@pytest.mark.asyncio
async def test_legacy_booking_can_initialize_memory(manager):
    manager.repository.get_customer_bookings = AsyncMock(return_value=[{
        "id": "older-booking", "status": "completed", "date": "2025-01-01", "start_time": "10:00",
        "service": {"name": "Painting Service", "category": "Painting"},
        "address": {"whatsapp_location": "Lugbe, Abuja"}, "provider": {"name": "Painter A"},
    }])
    result = await manager.handle_incoming_message(PHONE, "hi")
    assert "Last time you looked for" in result["text"] and "Lugbe, Abuja" in result["text"]
    assert get_memory(manager.repository.users[PHONE])["lastBooking"]["service"] == "Painting"


@pytest.mark.asyncio
async def test_failed_memory_write_does_not_block_search(manager):
    manager.repository.update_usage_memory = AsyncMock(side_effect=RuntimeError("database unavailable"))
    await session_manager.update_session(PHONE, {**DETAILS, "date": "2099-11-18", "step": "AWAITING_CONFIRMATION"})
    await manager.handle_incoming_message(PHONE, "yes")
    manager.repository.find_matching_services.assert_awaited_once()


def test_memory_summary_is_bounded_and_internal_ids_are_not_sent_to_gemini():
    memory = {}
    for i in range(30):
        memory = record_usage(memory, {"service": f"Service {i}", "location": f"Area {i}"}, {"id": f"internal-booking-{i}"})
    assert len(memory["recentServices"]) == 5 and len(memory["recentLocations"]) == 3
    assert len(memory["bookingIds"]) == 20 and len(memory["serviceCounts"]) <= 10
    public = prompt_memory(memory)
    assert "bookingIds" not in public
    prompt = ai_service.build_prompt("same place again", {"usageMemory": public})
    assert "internal-booking" not in prompt
    assert clean_memory({"recentServices": "wrong shape"})["recentServices"] == []


@pytest.mark.asyncio
async def test_one_users_preferences_do_not_appear_for_another_user(manager):
    await manager.repository.update_usage_memory(manager.repository.users[PHONE], details=DETAILS)
    other = "2348098765432"
    manager.repository.users[other] = {"id": "other", "name": "Ada", "phone": other, "role": "customer", "profile": {"onboarded": True}}
    result = await manager.handle_incoming_message(other, "memory")
    assert "Painting" not in result["text"] and "Lugbe" not in result["text"]
