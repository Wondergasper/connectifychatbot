import httpx
import pytest
from unittest.mock import AsyncMock

from app.core.config import settings
from app.main import app
from app.whatsapp.session import session_manager
from app.whatsapp.service import whatsapp_service
from app.whatsapp.wasender_webhook import conversation_manager


@pytest.mark.asyncio
async def test_wasender_webhook_authentication_and_menu_selection(monkeypatch):
    monkeypatch.setattr(settings, "WASENDER_WEBHOOK_SECRET", "secret")
    handler = AsyncMock()
    monkeypatch.setattr(conversation_manager, "handle_incoming_message", handler)
    await session_manager.update_session("2348001234567", {"wasenderChoices": [{"id": "ROLE_CUSTOMER", "title": "Customer"}]})
    payload = {"event": "messages.received", "data": {"messages": {"key": {"remoteJid": "2348001234567@s.whatsapp.net", "fromMe": False}, "messageBody": "1"}}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/wasender/webhook", json=payload)).status_code == 403
        assert (await client.post("/api/wasender/webhook", json=payload, headers={"X-Webhook-Signature": "secret"})).status_code == 200
    handler.assert_awaited_once_with("2348001234567", {"type": "interactive", "text": "Customer", "actionId": "ROLE_CUSTOMER"})
    assert (await session_manager.get_session("2348001234567"))["wasenderChoices"] == []


@pytest.mark.asyncio
async def test_wasender_menu_sends_numbered_text(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "wasender")
    sender = AsyncMock(return_value={"status": True})
    monkeypatch.setattr(whatsapp_service, "send_message", sender)
    await whatsapp_service.send_interactive_buttons("2348001234567", "Choose", [{"id": "ROLE_CUSTOMER", "title": "Customer"}])
    sender.assert_awaited_once_with("2348001234567", "Choose\n\n1. Customer\nReply with the number of your choice.")
    assert (await session_manager.get_session("2348001234567"))["wasenderChoices"][0]["id"] == "ROLE_CUSTOMER"
