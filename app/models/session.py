from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ConversationSession(BaseModel):
    step: str = "init"  # ONBOARDING, READY, PROVIDER_SETUP_CATEGORY, PROVIDER_SETUP_PRICE, AWAITING_CONFIRMATION, AWAITING_DATETIME, PROVIDER_SELECTION
    service: Optional[str] = None
    location: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    duration: int = 60
    urgency: str = "normal"
    budget: Optional[str] = None
    is_confirmed: bool = False
    ai_calls: int = 0
    matched_providers: List[Dict[str, Any]] = Field(default_factory=list)
    provider_category: Optional[str] = None
