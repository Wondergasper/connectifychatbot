import pytest
import hmac
import hashlib
import json
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings

client = TestClient(app)


def test_simulator_gating_in_test_environment():
    # In test/dev environment, simulator route is mounted
    response = client.get("/simulator")
    assert response.status_code == 200


def test_paystack_webhook_invalid_signature():
    payload = json.dumps({"event": "charge.success", "data": {}}).encode("utf-8")
    response = client.post(
        "/api/paystack/webhook",
        content=payload,
        headers={"x-paystack-signature": "invalid_sig_123"},
    )
    assert response.status_code == 400


def test_paystack_webhook_valid_signature_with_secret(monkeypatch):
    test_secret = "test_paystack_secret_key"
    from app.services.paystack_service import paystack_service
    monkeypatch.setattr(paystack_service, "secret_key", test_secret)

    payload_data = {
        "event": "charge.success",
        "data": {
            "reference": "TEST_REF_100",
            "amount": 500000,
            "currency": "NGN",
            "metadata": {"booking_id": "mock_bk_123"},
        },
    }
    raw_payload = json.dumps(payload_data).encode("utf-8")
    valid_sig = hmac.new(test_secret.encode("utf-8"), raw_payload, hashlib.sha512).hexdigest()

    response = client.post(
        "/api/paystack/webhook",
        content=raw_payload,
        headers={
            "x-paystack-signature": valid_sig,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.text == "EVENT_RECEIVED"
