from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class WhatsAppButtonReply(BaseModel):
    id: str
    title: str


class WhatsAppListReply(BaseModel):
    id: str
    title: str
    description: Optional[str] = None


class WhatsAppInteractive(BaseModel):
    type: str
    button_reply: Optional[WhatsAppButtonReply] = None
    list_reply: Optional[WhatsAppListReply] = None


class WhatsAppLocation(BaseModel):
    latitude: float
    longitude: float
    name: Optional[str] = None
    address: Optional[str] = None


class WhatsAppText(BaseModel):
    body: str


class WhatsAppMessage(BaseModel):
    from_: str = Field(alias="from")
    id: str
    timestamp: str
    type: str
    text: Optional[WhatsAppText] = None
    interactive: Optional[WhatsAppInteractive] = None
    location: Optional[WhatsAppLocation] = None

    class Config:
        populate_by_name = True


class WhatsAppValue(BaseModel):
    messaging_product: str
    metadata: Dict[str, Any]
    contacts: Optional[List[Dict[str, Any]]] = None
    messages: Optional[List[WhatsAppMessage]] = None


class WhatsAppChange(BaseModel):
    value: WhatsAppValue
    field: str


class WhatsAppEntry(BaseModel):
    id: str
    changes: List[WhatsAppChange]


class WhatsAppWebhookPayload(BaseModel):
    object: str
    entry: List[WhatsAppEntry]


class NormalizedIncomingMessage(BaseModel):
    type: str  # text, interactive, location
    text: str = ""
    action_id: Optional[str] = None
    button_id: Optional[str] = None
    list_id: Optional[str] = None
    location: Optional[WhatsAppLocation] = None
    raw: Optional[Dict[str, Any]] = None
