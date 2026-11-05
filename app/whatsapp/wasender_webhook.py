import hmac
import logging

from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.whatsapp.conversation import conversation_manager
from app.whatsapp.session import session_manager

logger = logging.getLogger("connectify.wasender_webhook")
router = APIRouter()


@router.post("")
@router.post("/")
async def receive_wasender_webhook(request: Request):
    secret = settings.WASENDER_WEBHOOK_SECRET
    signature = request.headers.get("x-webhook-signature", "")
    if not secret or not hmac.compare_digest(signature, secret):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict) or payload.get("event") != "messages.received":
        return {"status": "ignored"}
    data = payload.get("data") or {}
    message = data.get("messages") or {}
    if not isinstance(message, dict):
        return {"status": "ignored"}
    key = message.get("key") or {}
    jid = key.get("remoteJid") or ""
    if key.get("fromMe") or jid.endswith("@g.us"):
        return {"status": "ignored"}
    if jid.endswith("@lid") and not (key.get("cleanedSenderPn") or key.get("senderPn")):
        return {"status": "ignored"}
    phone = key.get("cleanedSenderPn") or key.get("senderPn") or jid
    phone = str(phone).split("@")[0].lstrip("+")
    if not phone.isdigit():
        return {"status": "ignored"}
    content = message.get("message") or {}
    location = content.get("locationMessage") or {}
    if location and location.get("degreesLatitude") is not None and location.get("degreesLongitude") is not None:
        incoming = {"type": "location", "location": {
            "latitude": location["degreesLatitude"], "longitude": location["degreesLongitude"],
            "name": location.get("name"), "address": location.get("address"),
        }}
    else:
        text = message.get("messageBody") or content.get("conversation") or (content.get("extendedTextMessage") or {}).get("text")
        if not isinstance(text, str) or not text.strip():
            return {"status": "ignored"}
        text = text.strip()
        incoming = {"type": "text", "text": text}
        session = await session_manager.get_session(phone)
        choices = session.get("wasenderChoices") or []
        if choices:
            await session_manager.update_session(phone, {"wasenderChoices": []})
            if text.isdigit() and 1 <= int(text) <= len(choices):
                choice = choices[int(text) - 1]
                incoming = {"type": "interactive", "text": choice.get("title", ""), "actionId": choice.get("id")}
    try:
        await conversation_manager.handle_incoming_message(phone, incoming)
    except Exception:
        logger.exception("WasenderAPI message processing failed")
        raise HTTPException(status_code=500, detail="Message processing failed")
    return {"status": "received"}
