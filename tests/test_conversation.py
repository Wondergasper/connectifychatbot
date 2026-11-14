import pytest
from app.whatsapp.conversation import ConversationManager, normalize_incoming
from app.whatsapp.session import session_manager
from app.repositories.connectify_repository import ConnectifyRepository


class DummyRepo(ConnectifyRepository):
    def __init__(self):
        super().__init__()
        self.users = {}
        self.bookings = {}
        self.services = []

    async def find_or_create_whatsapp_user(self, phone_number: str):
        if phone_number not in self.users:
            self.users[phone_number] = {
                "id": f"user_{phone_number.replace('+', '')}",
                "name": None,
                "phone": phone_number,
                "role": "customer",
                "profile": {},
                "wallet_balance": 15000,
                "wallet_currency": "NGN",
                "is_active": True,
            }
        return self.users[phone_number]

    async def update_user_name(self, user_id: str, full_name: str):
        for user in self.users.values():
            if user["id"] == user_id:
                user["name"] = full_name
                user["profile"] = {"fullName": full_name, "profileCompleted": True}
                return user
        return {"id": user_id, "name": full_name}

    async def update_user_role(self, user_id: str, role: str):
        for user in self.users.values():
            if user["id"] == user_id:
                user["role"] = role
                return user
        return {"id": user_id, "role": role}

    async def update_user_location(self, user_id: str, label: str, coordinates=None):
        for user in self.users.values():
            if user["id"] == user_id:
                user["profile"]["savedLocation"] = label
                return user
        return {"id": user_id}

    async def create_provider_service(self, provider_id: str, name: str, category: str, price: float, location: any, description: str = None):
        svc = {
            "id": f"svc_{len(self.services) + 1}",
            "provider_id": provider_id,
            "name": name,
            "category": category,
            "price": price,
            "location": location,
        }
        self.services.append(svc)
        return svc

    async def get_booking_by_id(self, booking_id: str):
        return {
            "id": booking_id,
            "provider_id": "prov_123",
            "customer_id": "user_2348022222222",
            "total_amount": 10000,
            "provider": {"id": "prov_123", "phone": "+2348011111111", "name": "Artisan Dave"},
            "customer": {"id": "user_2348022222222", "phone": "+2348022222222", "name": "Customer John"},
        }

    async def update_booking_status_atomic(self, booking_id: str, status: str, actor_id: str):
        return {
            "id": booking_id,
            "status": status,
            "total_amount": 10000,
            "provider": {"phone": "+2348011111111", "name": "Artisan Dave"},
            "customer": {"phone": "+2348022222222", "name": "Customer John"},
            "date": "2026-10-20",
        }

    async def complete_booking_with_escrow_payout(self, booking_id: str, provider_id: str, actor_id: str, commission_rate: float = 0.10):
        total = 10000.0
        comm = round(total * commission_rate)
        payout = total - comm
        return {
            "booking": {"id": booking_id, "status": "completed"},
            "totalAmount": total,
            "commission": comm,
            "payoutAmount": payout,
            "walletResult": {"success": True},
        }


@pytest.fixture
def manager():
    return ConversationManager(repository=DummyRepo())


@pytest.mark.asyncio
async def test_onboarding_name_flow(manager):
    phone = "+2348099887766"
    await session_manager.clear_session(phone)

    # 1. First interaction: Prompt for name
    res = await manager.handle_incoming_message(phone, "hi")
    assert res is not None

    # 2. Provide name: "Emeka Okafor"
    res2 = await manager.handle_incoming_message(phone, "Emeka Okafor")
    assert res2 is not None
    # User should now have name updated
    user = await manager.repository.find_or_create_whatsapp_user(phone)
    assert user["name"] == "Emeka Okafor"


@pytest.mark.asyncio
async def test_role_selection_customer(manager):
    phone = "+2348099887766"
    await session_manager.clear_session(phone)
    user = await manager.repository.find_or_create_whatsapp_user(phone)
    await manager.repository.update_user_name(user["id"], "Emeka Okafor")

    # Select customer role -> prompts for location
    res = await manager.handle_incoming_message(phone, {"actionId": "ROLE_CUSTOMER"})
    assert res is not None
    session = await session_manager.get_session(phone)
    assert session.get("step") == "CUSTOMER_SETUP_LOCATION"

    # Provide location "Ikeja" -> ready!
    res_loc = await manager.handle_incoming_message(phone, "Ikeja")
    assert res_loc is not None
    session_ready = await session_manager.get_session(phone)
    assert session_ready.get("step") == "READY"


@pytest.mark.asyncio
async def test_role_selection_provider_setup(manager):
    phone = "+2348055443322"
    await session_manager.clear_session(phone)
    await manager.handle_incoming_message(phone, "Tunde Baker")

    # Select provider role
    await manager.handle_incoming_message(phone, {"actionId": "ROLE_PROVIDER"})
    session = await session_manager.get_session(phone)
    assert session.get("step") == "PROVIDER_SETUP_CATEGORY"

    # Select trade: Plumbing -> prompts for coverage location
    await manager.handle_incoming_message(phone, {"actionId": "TRADE_PLUMBING"})
    session2 = await session_manager.get_session(phone)
    assert session2.get("step") == "PROVIDER_SETUP_LOCATION"
    assert session2.get("providerCategory") == "Plumbing"

    # Provide coverage area: "Surulere & Yaba" -> prompts for price
    await manager.handle_incoming_message(phone, "Surulere & Yaba")
    session_loc = await session_manager.get_session(phone)
    assert session_loc.get("step") == "PROVIDER_SETUP_PRICE"

    # Provide hourly rate: "7500"
    await manager.handle_incoming_message(phone, "7500")
    session3 = await session_manager.get_session(phone)
    assert session3.get("step") == "READY"
    assert len(manager.repository.services) == 1
    assert manager.repository.services[0]["price"] == 7500.0


@pytest.mark.asyncio
async def test_escrow_completion_10_percent_cut(manager):
    phone = "+2348022222222"
    user = await manager.repository.find_or_create_whatsapp_user(phone)
    res = await manager.handle_complete_booking_and_escrow(phone, user, "bk_999")

    assert res["totalAmount"] == 10000.0
    assert res["commission"] == 1000.0  # 10% platform commission
    assert res["payoutAmount"] == 9000.0  # 90% net payout to artisan


@pytest.mark.asyncio
async def test_dispute_keyword_detection(manager):
    phone = "+2348022222222"
    res = await manager.handle_incoming_message(phone, "The technician broke my generator, this is fraud!")
    assert res is not None


@pytest.mark.asyncio
async def test_location_pin_handling(manager):
    phone = "+2348022222222"
    loc_payload = {
        "type": "location",
        "location": {
            "latitude": 6.5244,
            "longitude": 3.3792,
            "name": "Ikeja City Mall",
            "address": "Obafemi Awolowo Way, Ikeja",
        },
    }
    res = await manager.handle_incoming_message(phone, loc_payload)
    assert res is not None
    session = await session_manager.get_session(phone)
    assert "Ikeja City Mall" in session.get("location")


@pytest.mark.asyncio
async def test_find_a_service_category_menu(manager):
    phone = "+2348088887777"
    await session_manager.clear_session(phone)
    await manager.handle_incoming_message(phone, "Chinwe Obi")

    # Click Find a Service button
    res = await manager.handle_incoming_message(phone, {"actionId": "CMD_FIND_SERVICE"})
    assert res is not None
    session = await session_manager.get_session(phone)
    assert session.get("step") == "CATEGORY_SELECTION"

    # Also test typing "find a service"
    res2 = await manager.handle_incoming_message(phone, "find a service")
    assert res2 is not None


@pytest.mark.asyncio
async def test_category_selection_and_location_flow(manager):
    phone = "+2348088887777"
    await session_manager.clear_session(phone)
    await manager.handle_incoming_message(phone, "Chinwe Obi")

    # Pick Plumbing from list
    await manager.handle_incoming_message(phone, {"actionId": "CATEGORY_PLUMBING"})
    session = await session_manager.get_session(phone)
    assert session.get("step") == "AWAITING_LOCATION"
    assert session.get("service") == "Plumbing"

    # Reply with location "Surulere"
    await manager.handle_incoming_message(phone, "Surulere")
    session2 = await session_manager.get_session(phone)
    assert session2.get("step") == "AWAITING_DATETIME"
    assert session2.get("location") == "Surulere"
