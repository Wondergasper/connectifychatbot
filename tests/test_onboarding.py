import pytest
from app.whatsapp.conversation import ConversationManager
from app.whatsapp.session import session_manager
from tests.test_conversation import DummyRepo


@pytest.fixture
def manager():
    return ConversationManager(repository=DummyRepo())


@pytest.mark.asyncio
async def test_complete_customer_onboarding_journey(manager):
    phone = "+2348011223344"
    await session_manager.clear_session(phone)

    # 1. New user sends any first greeting
    res1 = await manager.handle_incoming_message(phone, "Hello")
    assert res1 is not None
    session1 = await session_manager.get_session(phone)
    assert session1.get("step") == "ONBOARDING"

    # 2. User provides full name
    res2 = await manager.handle_incoming_message(phone, "Adaeze Uche")
    assert res2 is not None
    user = await manager.repository.find_or_create_whatsapp_user(phone)
    assert user["name"] == "Adaeze Uche"

    # 3. User chooses customer role
    res3 = await manager.handle_incoming_message(phone, {"actionId": "ROLE_CUSTOMER"})
    assert res3 is not None
    session3 = await session_manager.get_session(phone)
    assert session3.get("step") == "CUSTOMER_SETUP_LOCATION"

    # 4. Customer sets their area (text)
    res4 = await manager.handle_incoming_message(phone, "Lekki Phase 1")
    assert res4 is not None
    session4 = await session_manager.get_session(phone)
    assert session4.get("step") == "READY"
    assert session4.get("location") == "Lekki Phase 1"


@pytest.mark.asyncio
async def test_complete_provider_onboarding_journey(manager):
    phone = "+2348099881122"
    await session_manager.clear_session(phone)

    # 1. New user greeting
    await manager.handle_incoming_message(phone, "Hi there")

    # 2. Name capture
    await manager.handle_incoming_message(phone, "Kunle Adeyemi")

    # 3. Choose provider role
    await manager.handle_incoming_message(phone, {"actionId": "ROLE_PROVIDER"})
    session = await session_manager.get_session(phone)
    assert session.get("step") == "PROVIDER_SETUP_CATEGORY"

    # 4. Select trade (e.g. Electrical)
    await manager.handle_incoming_message(phone, {"actionId": "TRADE_ELECTRICAL"})
    session2 = await session_manager.get_session(phone)
    assert session2.get("step") == "PROVIDER_SETUP_LOCATION"
    assert session2.get("providerCategory") == "Electrical"

    # 5. Set coverage area
    await manager.handle_incoming_message(phone, "Ikeja and Maryland")
    session3 = await session_manager.get_session(phone)
    assert session3.get("step") == "PROVIDER_SETUP_PRICE"

    # 6. Set rate / price
    await manager.handle_incoming_message(phone, "8000")
    session4 = await session_manager.get_session(phone)
    assert session4.get("step") == "READY"

    # Verify the service was registered
    services = manager.repository.services
    created = [s for s in services if s["provider_id"] == f"user_{phone.replace('+', '')}"]
    assert len(created) == 1
    assert created[0]["category"] == "Electrical"
    assert created[0]["price"] == 8000.0


@pytest.mark.asyncio
async def test_unauthorized_escrow_release_rejected(manager):
    # Provider tries to release their own escrow
    phone_provider = "+2348011111111"
    user_provider = {
        "id": "prov_123",
        "name": "Artisan Dave",
        "phone": phone_provider,
        "role": "provider"
    }
    res = await manager.handle_complete_booking_and_escrow(phone_provider, user_provider, "bk_999")
    # Should be rejected with warning message
    assert "text" in res
    assert "Only the customer" in res["text"]
