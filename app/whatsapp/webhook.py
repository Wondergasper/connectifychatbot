import hmac
import hashlib
import logging
import json
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, HTTPException, Query, Response, status
from app.core.config import settings
from app.whatsapp.conversation import conversation_manager

logger = logging.getLogger("connectify.webhook")
router = APIRouter()


def is_valid_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    app_secret = settings.WHATSAPP_APP_SECRET
    if not app_secret:
        # In production, fail closed
        if settings.is_production:
            logger.error("[WhatsAppWebhook] WHATSAPP_APP_SECRET is not configured; rejecting in production.")
            return False
        return True

    if not signature_header:
        logger.error("[WhatsAppWebhook] Missing X-Hub-Signature-256 header; rejecting.")
        return False

    expected_sig = "sha256=" + hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature_header, expected_sig)


def extract_message_data(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not message:
        return None

    msg_type = message.get("type")

    if msg_type == "text":
        body = (message.get("text") or {}).get("body", "").strip()
        if not body:
            return None
        return {
            "type": "text",
            "text": body,
            "raw": message,
        }

    if msg_type == "interactive":
        interactive = message.get("interactive") or {}
        button = interactive.get("button_reply") or {}
        list_reply = interactive.get("list_reply") or {}
        button_id = button.get("id")
        list_id = list_reply.get("id")
        text = button.get("title") or list_reply.get("title") or button_id or list_id or ""
        return {
            "type": "interactive",
            "text": text,
            "buttonId": button_id,
            "listId": list_id,
            "actionId": button_id or list_id,
            "raw": message,
        }

    if msg_type == "location":
        loc = message.get("location") or {}
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        text = loc.get("name") or loc.get("address") or f"{lat},{lng}"
        return {
            "type": "location",
            "text": text,
            "location": {
                "latitude": lat,
                "longitude": lng,
                "name": loc.get("name"),
                "address": loc.get("address"),
            },
            "raw": message,
        }

    return None


@router.get("")
@router.get("/")
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """
    Meta Webhook Verification Challenge endpoint.
    """
    verify_token = settings.VERIFY_TOKEN
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        logger.info("[WhatsAppWebhook] Webhook subscription verified successfully.")
        return Response(content=hub_challenge or "", media_type="text/plain", status_code=status.HTTP_200_OK)

    logger.warning("[WhatsAppWebhook] Verification challenge failed.")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification token mismatch")


@router.post("")
@router.post("/")
async def receive_webhook(request: Request):
    """
    Meta WhatsApp Cloud API Webhook receiver endpoint.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-hub-signature-256")

    if not is_valid_signature(raw_body, signature):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook signature")

    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception as e:
        logger.error(f"[WhatsAppWebhook] JSON decode error: {e}")
        return Response(content="OK", status_code=status.HTTP_200_OK)

    if body.get("object") != "whatsapp_business_account":
        return Response(content="OK", status_code=status.HTTP_200_OK)

    try:
        entries = body.get("entry", [])
        if not entries:
            return Response(content="OK", status_code=status.HTTP_200_OK)

        changes = entries[0].get("changes", [])
        if not changes:
            return Response(content="OK", status_code=status.HTTP_200_OK)

        value = changes[0].get("value", {})
        messages_list = value.get("messages", [])
        if not messages_list:
            return Response(content="OK", status_code=status.HTTP_200_OK)

        incoming_msg = messages_list[0]
        sender_phone = incoming_msg.get("from")
        extracted = extract_message_data(incoming_msg)

        if sender_phone and extracted:
            await conversation_manager.handle_incoming_message(sender_phone, extracted)

    except Exception as e:
        logger.error(f"[WhatsAppWebhook] Processing error: {e}", exc_info=True)

    # Always return HTTP 200 quickly to Meta
    return Response(content="EVENT_RECEIVED", status_code=status.HTTP_200_OK)
