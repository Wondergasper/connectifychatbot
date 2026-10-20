from typing import Optional, Dict, Any
from pydantic import BaseModel, EmailStr, Field


class UserProfile(BaseModel):
    full_name: Optional[str] = None
    role_selected: bool = False
    saved_location: Optional[str] = None
    location_coordinates: Optional[Dict[str, float]] = None
    whatsapp_id: Optional[str] = None


class User(BaseModel):
    id: str
    name: Optional[str] = None
    email: Optional[str] = None
    phone: str
    role: str = "customer"  # customer, provider, admin
    profile: Optional[Dict[str, Any]] = Field(default_factory=dict)
    wallet_balance: float = 0.0
    wallet_currency: str = "NGN"
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
