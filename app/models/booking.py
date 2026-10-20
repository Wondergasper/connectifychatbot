from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class Service(BaseModel):
    id: str
    provider_id: str
    name: str
    category: str
    description: str
    price: float
    price_type: str = "hourly"
    duration_minutes: int = 60
    location: Optional[Dict[str, Any]] = None
    is_active: bool = True


class Booking(BaseModel):
    id: str
    customer_id: str
    provider_id: str
    service_id: str
    date: str
    time: str
    duration: int = 60
    total_amount: float = 0.0
    status: str = "pending"  # pending, confirmed, in_progress, completed, cancelled
    notes: Optional[str] = None
    address: Optional[Dict[str, Any]] = None
    customer: Optional[Dict[str, Any]] = None
    provider: Optional[Dict[str, Any]] = None
    service: Optional[Dict[str, Any]] = None


class EscrowPayoutResult(BaseModel):
    booking_id: str
    provider_id: str
    total_amount: float
    platform_commission: float
    payout_amount: float
    status: str = "completed"
