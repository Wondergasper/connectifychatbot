import hmac
import hashlib
import json
import logging
from typing import Dict, Any

from fastapi import APIRouter, Request, HTTPException, status, Response
from app.core.config import settings
from app.services.paystack_service import paystack_service
from app.repositories.connectify_repository import ConnectifyRepository
from app.whatsapp.service import whatsapp_service
from app.whatsapp import messages

logger = logging.getLogger("connectify.paystack_webhook")
router = APIRouter()
repository = ConnectifyRepository()


@router.post("")
@router.post("/")
async def handle_paystack_webhook(request: Request):
    """
    Receives and processes Paystack Webhook events (e.g. charge.success)
    with strict HMAC SHA512 signature verification.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature")

    # 1. Verify signature
    if not paystack_service.verify_webhook_signature(raw_body, signature):
        logger.warning("[PaystackWebhook] Invalid or missing signature header.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook signature",
        )

    try:
        event = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as e:
        logger.error(f"[PaystackWebhook] JSON decode error: {e}")
        return Response(content="Bad Request", status_code=status.HTTP_400_BAD_REQUEST)

    event_type = event.get("event")
    data = event.get("data") or {}

    logger.info(f"[PaystackWebhook] Received event: {event_type}")

    if event_type == "charge.success":
        reference = data.get("reference")
        amount_kobo = data.get("amount") or 0
        currency = data.get("currency") or "NGN"
        metadata = data.get("metadata") or {}
        booking_id = metadata.get("booking_id")

        if reference and booking_id:
            try:
                # Atomically update booking to confirmed and payment status to 'held' (escrow)
                client = repository.client
                if client:
                    client.rpc("mark_booking_paid_atomic", {
                        "p_booking_id": booking_id,
                        "p_reference": reference,
                        "p_amount_kobo": amount_kobo,
                        "p_currency": currency,
                    }).execute()

                booking = await repository.get_booking_by_id(booking_id)
                if booking:
                    customer = booking.get("customer") or {}
                    provider = booking.get("provider") or {}

                    # Notify Customer
                    if customer.get("phone"):
                        await whatsapp_service.send_message(
                            customer["phone"],
                            f"💳 *Payment Received & Secured in Escrow!*\n\n"
                            f"₦{amount_kobo / 100:,.0f} has been placed in Escrow for booking #{booking_id[:8]}.\n"
                            "Your artisan has been notified to proceed. Funds will only be released after your job completion confirmation!",
                        )

                    # Notify Provider
                    if provider.get("phone"):
                        await whatsapp_service.send_message(
                            provider["phone"],
                            f"🔔 *Job Funded & Confirmed!*\n\n"
                            f"Customer payment of ₦{amount_kobo / 100:,.0f} has been verified and secured in Escrow.\n"
                            f"Scheduled Date: {booking.get('date')} at {booking.get('start_time') or 'scheduled time'}.\n"
                            "You may now proceed with the job!",
                        )

            except Exception as err:
                logger.error(f"[PaystackWebhook] Error processing charge.success: {err}", exc_info=True)

    return Response(content="EVENT_RECEIVED", status_code=status.HTTP_200_OK)
