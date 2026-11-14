import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_get_simulator_page():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/simulator")
        assert res.status_code == 200
        assert "Connectify Nigeria - WhatsApp Web Simulator" in res.text
        assert "chat-container" in res.text or "app-container" in res.text


@pytest.mark.asyncio
async def test_simulator_message_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "phone": "+2348011223344",
            "text": "Hello",
        }
        res = await client.post("/api/simulator/message", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert isinstance(data.get("messages"), list)
        assert len(data["messages"]) > 0


@pytest.mark.asyncio
async def test_simulator_reset_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/simulator/reset", json={"phone": "+2348011223344"})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "cleared" in data["message"].lower()
