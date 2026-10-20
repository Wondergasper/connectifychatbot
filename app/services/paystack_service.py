import hmac
import hashlib
import logging
from typing import Dict, Any, Optional

import httpx
from app.core.config import settings

logger = logging.getLogger("connectify.paystack")

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackService:
    def __init__(self):
        self.secret_key = settings.PAYSTACK_SECRET_KEY

    async def initialize_transaction(
        self, email: str, amount_kobo: int, metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Initializes a Paystack checkout transaction.
        Amount must be in kobo (e.g. 5000 NGN = 500000 kobo).
        """
        if not self.secret_key:
            simulated_ref = f"SIM_PAY_{int(amount_kobo)}_{metadata.get('user_id', 'anon') if metadata else 'anon'}"
            simulated_url = f"https://checkout.paystack.com/simulate/{simulated_ref}"
            logger.warning(f"[PaystackService] Simulated transaction initialization: {simulated_url}")
            return {
                "status": True,
                "message": "Authorization URL created (Simulated)",
                "data": {
                    "authorization_url": simulated_url,
                    "access_code": f"sim_access_{simulated_ref}",
                    "reference": simulated_ref,
                },
            }

        url = f"{PAYSTACK_BASE_URL}/transaction/initialize"
        payload = {
            "email": email,
            "amount": amount_kobo,
            "metadata": metadata or {},
        }
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"[PaystackService] Init transaction error: {e}")
            raise

    async def verify_transaction(self, reference: str) -> Dict[str, Any]:
        """
        Verifies a transaction using its reference.
        """
        if not self.secret_key:
            return {
                "status": True,
                "data": {
                    "status": "success",
                    "reference": reference,
                    "amount": 500000,
                    "currency": "NGN",
                },
            }

        url = f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}"
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"[PaystackService] Verify transaction error: {e}")
            raise

    def verify_webhook_signature(self, payload_bytes: bytes, signature_header: Optional[str]) -> bool:
        secret = self.secret_key or settings.PAYSTACK_SECRET_KEY
        if not secret:
            if settings.is_production:
                logger.error("[PaystackService] PAYSTACK_SECRET_KEY is not configured; rejecting in production.")
                return False
            # In test mode without secret key configured, reject forged signatures
            return bool(signature_header and signature_header == "test_dummy_signature")

        if not signature_header:
            return False

        computed = hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha512
        ).hexdigest()

        return hmac.compare_digest(signature_header, computed)


paystack_service = PaystackService()
