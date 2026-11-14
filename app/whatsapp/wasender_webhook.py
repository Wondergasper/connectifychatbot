import hmac
import logging
import math

from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.whatsapp.wasender_queue import wasender_queue

logger = logging.getLogger("connectify.wasender_webhook")
router = APIRouter()


def extract_incoming(payload):
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload must be an object")
    if payload.get("event") != "messages.received":
        return None
    data = payload.get("data")
    message = data.get("messages") if isinstance(data, dict) else None
    key = message.get("key") if isinstance(message, dict) else None
    if not isinstance(key, dict):
        raise ValueError("Missing message key")
    jid = key.get("remoteJid") or ""
    if not isinstance(jid, str):
        raise ValueError("Invalid sender")
    if key.get("fromMe") or jid.endswith("@g.us"):
        return None
    if jid.endswith("@lid") and not (key.get("cleanedSenderPn") or key.get("senderPn")):
        return None
    phone = str(key.get("cleanedSenderPn") or key.get("senderPn") or jid).split("@")[0].lstrip("+")
    if not phone.isascii() or not phone.isdigit() or not 7 <= len(phone) <= 15:
        return None
    content = message.get("message") or {}
    if not isinstance(content, dict):
        raise ValueError("Invalid message content")
    location = content.get("locationMessage")
    if isinstance(location, dict):
        try:
            lat, lng = float(location["degreesLatitude"]), float(location["degreesLongitude"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Invalid coordinates")
        if not math.isfinite(lat) or not math.isfinite(lng) or not -90 <= lat <= 90 or not -180 <= lng <= 180:
            raise ValueError("Invalid coordinates")
        incoming = {"type": "location", "location": {
            "latitude": lat, "longitude": lng,
            "name": location.get("name"), "address": location.get("address"),
        }}
    else:
        extended = content.get("extendedTextMessage") or {}
        text = message.get("messageBody") or content.get("conversation") or (extended.get("text") if isinstance(extended, dict) else None)
        if not isinstance(text, str) or not text.strip():
            return None
        incoming = {"type": "text", "text": text.strip()}
    message_id = key.get("id")
    if not isinstance(message_id, str) or not message_id.strip() or len(message_id) > 256:
        raise ValueError("Missing or invalid message ID")
    return message_id, phone, incoming


@router.post("")
@router.post("/")
async def receive_wasender_webhook(request: Request):
    secret = settings.WASENDER_WEBHOOK_SECRET
    signature = request.headers.get("x-webhook-signature", "")
    if not secret or not hmac.compare_digest(signature.encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    try:
        extracted = extract_incoming(await request.json())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    if extracted is None:
        return {"status": "ignored"}
    try:
        inserted = await wasender_queue.enqueue_incoming(*extracted)
    except Exception:
        logger.error("Cannot persist Wasender webhook; not acknowledging receipt")
        raise HTTPException(status_code=503, detail="Message queue unavailable")
    return {"status": "received" if inserted else "duplicate"}
