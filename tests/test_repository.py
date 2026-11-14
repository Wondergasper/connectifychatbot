import pytest
from app.repositories.connectify_repository import (
    normalize_phone,
    format_price,
    quote_postgrest_value,
    ConnectifyRepository,
)


def test_normalize_phone():
    assert normalize_phone("2348012345678") == "+2348012345678"
    assert normalize_phone("+2348012345678") == "+2348012345678"
    assert normalize_phone(" 2348012345678 ") == "+2348012345678"


def test_format_price():
    assert format_price(5000) == "NGN 5,000"
    assert format_price(1500000) == "NGN 1,500,000"
    assert format_price(2500.50) == "NGN 2,500.50"
    assert format_price(None) is None
    assert format_price("") is None


def test_quote_postgrest_value():
    assert quote_postgrest_value("Lagos") == '"Lagos"'
    assert quote_postgrest_value('quote "test"') == '"quote \\"test\\""'
    assert quote_postgrest_value(None) == "null"


@pytest.mark.asyncio
async def test_simulated_repository_without_supabase():
    repo = ConnectifyRepository(client=None)

    # 1. find_or_create_whatsapp_user
    user = await repo.find_or_create_whatsapp_user("+2348012345678")
    assert user["phone"] == "+2348012345678"
    assert user["role"] == "customer"

    # 2. update_user_name
    updated_name = await repo.update_user_name(user["id"], "John Doe")
    assert updated_name["name"] == "John Doe"

    # 3. update_user_role
    updated_role = await repo.update_user_role(user["id"], "provider")
    assert updated_role["role"] == "provider"

    # 4. wallet balance
    wallet = await repo.get_user_wallet(user["id"])
    assert wallet["currency"] == "NGN"

    # 5. create service
    svc = await repo.create_provider_service(
        provider_id=user["id"],
        name="Plumbing Service",
        category="Plumbing",
        price=6000,
        location="Ikeja",
    )
    assert svc["price"] == 6000.0


@pytest.mark.asyncio
async def test_database_wait_does_not_block_webhook_event_loop():
    import asyncio
    import threading
    from types import SimpleNamespace

    started, released = threading.Event(), threading.Event()

    class Query:
        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def single(self):
            return self

        def execute(self):
            started.set()
            assert released.wait(2), "Database query blocked the event loop"
            return SimpleNamespace(data={"wallet_balance": 100, "wallet_currency": "NGN"})

    async def receive_another_request():
        assert await asyncio.to_thread(started.wait, 2)
        released.set()

    repo = ConnectifyRepository(client=SimpleNamespace(from_=lambda table: Query()))
    wallet, _ = await asyncio.gather(repo.get_user_wallet("user-1"), receive_another_request())
    assert wallet["balance"] == 100
