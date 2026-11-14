import hmac
import hashlib
import json
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings


@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert "Connectify" in data["service"]


@pytest.mark.asyncio
async def test_webhook_verification_success():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": settings.VERIFY_TOKEN,
                "hub.challenge": "1122334455",
            },
        )
        assert res.status_code == 200
        assert res.text == "1122334455"


@pytest.mark.asyncio
async def test_webhook_verification_failure():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "1122334455",
            },
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_webhook_post_signature_validation():
    transport = ASGITransport(app=app)
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "+2348012345678",
                                    "id": "wamid.123",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    raw_body = json.dumps(payload).encode("utf-8")
    valid_sig = "sha256=" + hmac.new(
        settings.WHATSAPP_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Invalid signature -> 403
        bad_res = await client.post(
            "/api/whatsapp/webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "x-hub-signature-256": "sha256=invalid",
            },
        )
        assert bad_res.status_code == 403

        # 2. Valid signature -> 200
        good_res = await client.post(
            "/api/whatsapp/webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "x-hub-signature-256": valid_sig,
            },
        )
        assert good_res.status_code == 200
        assert good_res.text == "EVENT_RECEIVED"
