import os
import logging
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.whatsapp.conversation import conversation_manager
from app.whatsapp.session import session_manager
from app.whatsapp.service import whatsapp_service

logger = logging.getLogger("connectify.simulator")
router = APIRouter()

HTML_PATH = os.path.join(os.path.dirname(__file__), "static", "index.html")


class SimulatorMessageRequest(BaseModel):
    phone: str
    text: Optional[str] = ""
    actionId: Optional[str] = None
    buttonId: Optional[str] = None
    listId: Optional[str] = None
    location: Optional[Dict[str, Any]] = None


class ResetRequest(BaseModel):
    phone: str


@router.get("/simulator", response_class=HTMLResponse)
@router.get("/simulator/", response_class=HTMLResponse)
async def get_simulator_page():
    """Serves the interactive WhatsApp Web Simulator HTML page."""
    if not os.path.exists(HTML_PATH):
        return HTMLResponse("<h1>Simulator static file not found</h1>", status_code=404)
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)


@router.post("/api/simulator/message")
async def send_simulator_message(req: SimulatorMessageRequest):
    """
    Receives user input from the HTML simulator, runs it through the
    real conversation manager and live Supabase database, and returns all
    outgoing messages and interactive buttons generated for the UI.
    """
    collected: List[Dict[str, Any]] = []

    def listener(to_phone: str, msg: Dict[str, Any]):
        collected.append(msg)

    whatsapp_service.add_listener(listener)
    try:
        incoming_payload = {
            "type": "location" if req.location else ("interactive" if (req.actionId or req.buttonId or req.listId) else "text"),
            "text": req.text or req.actionId or "",
            "actionId": req.actionId or req.buttonId or req.listId,
            "buttonId": req.buttonId or req.actionId,
            "listId": req.listId or req.actionId,
            "location": req.location,
        }

        await conversation_manager.handle_incoming_message(req.phone, incoming_payload)
        return {"success": True, "messages": collected}
    except Exception as e:
        logger.error(f"[Simulator] Error processing message: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "messages": [
                {
                    "type": "text",
                    "text": f"⚠️ Simulator Error: {str(e)}",
                }
            ],
        }
    finally:
        whatsapp_service.remove_listener(listener)


@router.post("/api/simulator/reset")
async def reset_simulator_session(req: ResetRequest):
    """Clears the session for the given phone number."""
    await session_manager.clear_session(req.phone)
    return {"success": True, "message": f"Session cleared for {req.phone}"}
