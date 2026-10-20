import logging
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import get_supabase_client

logger = logging.getLogger("connectify.repository")

WHATSAPP_USER_SELECT = (
    "id,name,email,phone,role,profile,provider_details,rating_average,"
    "rating_count,completed_jobs_count,wallet_balance,wallet_currency,"
    "is_active,created_at,updated_at"
)


def normalize_phone(phone_number: str = "") -> str:
    trimmed = str(phone_number or "").strip()
    return trimmed if trimmed.startswith("+") else f"+{trimmed}"


def quote_postgrest_value(val: Any) -> str:
    if val is None:
        return "null"
    escaped = str(val).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_price(price: Any) -> Optional[str]:
    if price is None or price == "":
        return None
    try:
        numeric = float(price)
        if numeric.is_integer():
            return f"NGN {int(numeric):,}"
        return f"NGN {numeric:,.2f}"
    except (ValueError, TypeError):
        return str(price)


class ConnectifyRepository:
    _SENTINEL = object()

    def __init__(self, client: Any = _SENTINEL):
        self._custom_client = client

    @property
    def client(self):
        if self._custom_client is not self._SENTINEL:
            return self._custom_client
        return get_supabase_client()

    async def find_user_by_phone(self, phone_number: str) -> Optional[Dict[str, Any]]:
        client = self.client
        if not client:
            return None

        phone = normalize_phone(phone_number)
        quoted_phone = quote_postgrest_value(phone)
        quoted_raw_phone = quote_postgrest_value(phone_number)

        response = (
            client.from_("app_users")
            .select(WHATSAPP_USER_SELECT)
            .or_(f"phone.eq.{quoted_phone},phone.eq.{quoted_raw_phone}")
            .maybe_single()
            .execute()
        )
        return response.data if response else None

    async def find_or_create_whatsapp_user(self, phone_number: str) -> Dict[str, Any]:
        existing = await self.find_user_by_phone(phone_number)
        if existing:
            return existing

        client = self.client
        phone = normalize_phone(phone_number)
        clean_num = phone.replace("+", "")

        payload = {
            "name": None,
            "email": f"whatsapp_{clean_num}@connectify.com",
            "phone": phone,
            "role": "customer",
            "source": "whatsapp",
            "is_active": True,
            "profile": {"whatsapp_id": phone_number, "onboarded": False},
        }

        if not client:
            # Simulated in-memory record for local testing without Supabase
            return {
                "id": str(uuid.uuid4()),
                **payload,
                "wallet_balance": 0,
                "wallet_currency": "NGN",
                "rating_average": 5.0,
                "rating_count": 0,
                "completed_jobs_count": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

        response = client.from_("app_users").insert(payload).execute()
        return response.data[0] if (response.data and isinstance(response.data, list)) else (response.data or {})

    async def update_user_name(self, user_id: str, full_name: str) -> Dict[str, Any]:
        client = self.client
        if not client:
            return {
                "id": user_id,
                "name": full_name,
                "profile": {"fullName": full_name, "profileCompleted": True},
            }

        user_res = (
            client.from_("app_users")
            .select(WHATSAPP_USER_SELECT)
            .eq("id", user_id)
            .single()
            .execute()
        )
        current_profile = (user_res.data or {}).get("profile") or {}
        updated_profile = {
            **current_profile,
            "fullName": full_name,
            "profileCompleted": True,
        }

        response = (
            client.from_("app_users")
            .update({"name": full_name, "profile": updated_profile})
            .eq("id", user_id)
            .execute()
        )
        return response.data[0] if (response.data and isinstance(response.data, list)) else (response.data or {})

    async def update_user_role(self, user_id: str, role: str) -> Dict[str, Any]:
        valid_role = role if role in ["customer", "provider"] else "customer"
        client = self.client
        if not client:
            return {
                "id": user_id,
                "role": valid_role,
                "profile": {"roleSelected": True},
            }

        user_res = (
            client.from_("app_users")
            .select(WHATSAPP_USER_SELECT)
            .eq("id", user_id)
            .single()
            .execute()
        )
        current_profile = (user_res.data or {}).get("profile") or {}
        updated_profile = {
            **current_profile,
            "roleSelected": True,
        }

        response = (
            client.from_("app_users")
            .update({"role": valid_role, "profile": updated_profile})
            .eq("id", user_id)
            .execute()
        )
        return response.data[0] if (response.data and isinstance(response.data, list)) else (response.data or {})

    async def update_user_location(
        self, user_id: str, label: str, coordinates: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        client = self.client
        if not client:
            return {
                "id": user_id,
                "profile": {
                    "savedLocation": label,
                    "locationCoordinates": coordinates,
                },
            }

        user_res = (
            client.from_("app_users")
            .select(WHATSAPP_USER_SELECT)
            .eq("id", user_id)
            .single()
            .execute()
        )
        current_profile = (user_res.data or {}).get("profile") or {}
        updated_profile = {
            **current_profile,
            "savedLocation": label,
            "locationCoordinates": coordinates or current_profile.get("locationCoordinates"),
        }

        response = (
            client.from_("app_users")
            .update({"profile": updated_profile})
            .eq("id", user_id)
            .execute()
        )
        return response.data[0] if (response.data and isinstance(response.data, list)) else (response.data or {})

    async def get_user_wallet(self, user_id: str) -> Dict[str, Any]:
        client = self.client
        if not client:
            return {"balance": 0.0, "currency": "NGN", "formatted": "NGN 0"}

        response = (
            client.from_("app_users")
            .select("id, wallet_balance, wallet_currency")
            .eq("id", user_id)
            .single()
            .execute()
        )
        data = response.data or {}
        balance = float(data.get("wallet_balance") or 0)
        currency = data.get("wallet_currency") or "NGN"
        return {
            "balance": balance,
            "currency": currency,
            "formatted": format_price(balance),
        }

    async def get_categories(self, limit: int = 10) -> List[Dict[str, Any]]:
        client = self.client
        if not client:
            return []

        response = (
            client.from_("categories")
            .select("id, name, description")
            .limit(limit)
            .execute()
        )
        return response.data or []

    async def find_matching_services(
        self, service: Optional[str] = None, location: Optional[str] = None, limit: int = 3
    ) -> List[Dict[str, Any]]:
        client = self.client
        if not client:
            return []

        query = (
            client.from_("services")
            .select(
                "*,provider:provider_id(id,name,email,phone,role,profile,provider_details,rating_average,rating_count,completed_jobs_count,wallet_balance,wallet_currency,is_active,created_at,updated_at)"
            )
            .eq("is_active", True)
            .limit(limit)
        )

        if service:
            term = str(service).strip()
            stem_map = {
                "plumber": "plumb",
                "plumbers": "plumb",
                "electrician": "electric",
                "electricians": "electric",
                "cleaner": "clean",
                "cleaners": "clean",
                "carpenter": "carpent",
                "carpenters": "carpent",
                "painter": "paint",
                "painters": "paint",
                "mechanic": "generator",
                "mechanics": "generator",
            }
            search_term = stem_map.get(term.lower(), term)
            escaped_service = search_term.replace("%", "\\%").replace("_", "\\_")
            quoted_term = quote_postgrest_value(f"%{escaped_service}%")
            query = query.or_(f"name.ilike.{quoted_term},category.ilike.{quoted_term},description.ilike.{quoted_term}")

        if location:
            escaped_loc = str(location).replace("%", "\\%").replace("_", "\\_")
            quoted_loc = quote_postgrest_value(f"%{escaped_loc}%")
            query = query.or_(f"location->>address.ilike.{quoted_loc},location->>city.ilike.{quoted_loc}")

        response = (
            query.order("rating_average", desc=True, nullsfirst=False)
            .order("rating_count", desc=True)
            .execute()
        )

        rows = response.data or []
        results = []
        for row in rows:
            provider = row.get("provider") or {}
            loc = row.get("location") or {}
            loc_label = loc.get("address") or loc.get("city") or "" if isinstance(loc, dict) else str(loc)
            results.append({
                "id": row.get("id"),
                "providerId": row.get("provider_id") or provider.get("id"),
                "displayName": provider.get("name") or "Connectify Provider",
                "category": row.get("category"),
                "price": row.get("price"),
                "priceLabel": format_price(row.get("price")),
                "locationLabel": loc_label,
                "rating": f"{float(provider.get('rating_average') or 5.0):.1f}",
                "completedJobs": int(provider.get("completed_jobs_count") or 0),
                "raw": row,
            })
        return results

    async def create_booking_request(
        self, customer_id: str, provider: Dict[str, Any], session: Dict[str, Any]
    ) -> Dict[str, Any]:
        client = self.client
        booking_id = str(uuid.uuid4())
        normalized_date = session.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
        normalized_time = session.get("time") or "09:00"

        if not client:
            return {
                "id": booking_id,
                "customer_id": customer_id,
                "provider_id": provider.get("providerId"),
                "date": normalized_date,
                "start_time": normalized_time,
                "total_amount": provider.get("price") or 0,
                "status": "pending",
                "customer": {"id": customer_id, "name": "Customer", "phone": "+2348000000000"},
                "provider": {"id": provider.get("providerId"), "name": provider.get("displayName"), "phone": "+2348011111111"},
            }

        # Invoke Postgres RPC 'create_booking_atomic'
        rpc_params = {
            "p_booking_id": booking_id,
            "p_customer_id": customer_id,
            "p_provider_id": provider.get("providerId"),
            "p_service_id": provider.get("id"),
            "p_date": normalized_date,
            "p_time": normalized_time,
            "p_duration": session.get("duration") or 60,
            "p_total_amount": provider.get("price") or 0,
            "p_notes": f"WhatsApp: {session.get('service')}" if session.get("service") else "WhatsApp Booking",
            "p_address": {"whatsapp_location": session.get("location") or "Unknown"},
        }

        rpc_res = client.rpc("create_booking_atomic", rpc_params).execute()
        if hasattr(rpc_res, "error") and rpc_res.error:
            raise Exception(rpc_res.error)

        # Fetch the created booking with provider and customer phones
        booking_res = (
            client.from_("bookings")
            .select("*, provider:provider_id(id, name, phone), customer:customer_id(id, name, phone)")
            .eq("id", booking_id)
            .single()
            .execute()
        )
        return booking_res.data

    async def get_booking_by_id(self, booking_id: str) -> Optional[Dict[str, Any]]:
        client = self.client
        if not client:
            return None

        response = (
            client.from_("bookings")
            .select("*, provider:provider_id(id, name, phone), customer:customer_id(id, name, phone), service:service_id(name, category)")
            .eq("id", booking_id)
            .single()
            .execute()
        )
        return response.data

    async def get_provider_pending_bookings(self, provider_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        client = self.client
        if not client:
            return []

        response = (
            client.from_("bookings")
            .select("*, customer:customer_id(name, phone), service:service_id(name)")
            .eq("provider_id", provider_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []

    async def get_provider_active_bookings(self, provider_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        client = self.client
        if not client:
            return []

        response = (
            client.from_("bookings")
            .select("*, customer:customer_id(name, phone), service:service_id(name)")
            .eq("provider_id", provider_id)
            .in_("status", ["confirmed", "in_progress"])
            .order("date", desc=False)
            .limit(limit)
            .execute()
        )
        return response.data or []

    async def get_customer_bookings(self, customer_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        client = self.client
        if not client:
            return []

        response = (
            client.from_("bookings")
            .select("*, provider:provider_id(name, phone), service:service_id(name)")
            .eq("customer_id", customer_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data or []

    async def update_booking_status_atomic(self, booking_id: str, status: str, actor_id: str) -> Dict[str, Any]:
        client = self.client
        if not client:
            return {"id": booking_id, "status": status, "actor_id": actor_id}

        rpc_res = client.rpc("update_booking_status_atomic", {
            "p_booking_id": booking_id,
            "p_status": status,
            "p_actor_id": actor_id,
            "p_cancellation_reason": None,
            "p_new_date": None,
            "p_new_time": None,
        }).execute()

        if hasattr(rpc_res, "error") and rpc_res.error:
            raise Exception(rpc_res.error)

        return await self.get_booking_by_id(booking_id)

    async def create_provider_service(
        self,
        provider_id: str,
        name: Optional[str],
        category: str,
        price: float,
        location: Any,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        client = self.client
        loc_obj = location if isinstance(location, dict) else {"address": str(location or "Lagos")}
        address_str = loc_obj.get("address") or loc_obj.get("city") or "Nigeria"

        service_payload = {
            "provider_id": provider_id,
            "name": name or f"{category} Service",
            "category": category,
            "description": description or f"Professional {category} services in {address_str}.",
            "price": float(price or 5000),
            "price_type": "hourly",
            "duration_minutes": 60,
            "location": loc_obj,
            "is_active": True,
        }

        if not client:
            return {"id": str(uuid.uuid4()), **service_payload}

        response = client.from_("services").insert(service_payload).execute()
        return response.data[0] if (response.data and isinstance(response.data, list)) else (response.data or {})

    async def complete_booking_with_escrow_payout(
        self, booking_id: str, provider_id: str, actor_id: str, commission_rate: Optional[float] = None
    ) -> Dict[str, Any]:
        comm_rate = commission_rate if commission_rate is not None else settings.PLATFORM_COMMISSION_RATE
        booking = await self.update_booking_status_atomic(booking_id, "completed", actor_id)

        total_amount = float((booking or {}).get("total_amount") or 0)
        if total_amount > 0:
            commission = round(total_amount * comm_rate)
            payout_amount = total_amount - commission
            # Deterministic reference based on booking ID to prevent duplicate payout attempts
            payout_ref = f"PAYOUT_{booking_id}"

            client = self.client
            wallet_result = None
            if client:
                rpc_res = client.rpc("create_manual_wallet_credit", {
                    "p_user_id": provider_id,
                    "p_amount": payout_amount,
                    "p_currency": "NGN",
                    "p_reference": payout_ref,
                    "p_description": f"Payout for Booking #{booking_id[:8]} ({round((1 - comm_rate) * 100)}% after platform commission)",
                    "p_metadata": {
                        "booking_id": booking_id,
                        "total_booking_amount": total_amount,
                        "platform_commission": commission,
                        "commission_rate": comm_rate,
                    },
                }).execute()
                wallet_result = getattr(rpc_res, "data", None)

            return {
                "booking": booking,
                "totalAmount": total_amount,
                "commission": commission,
                "payoutAmount": payout_amount,
                "walletResult": wallet_result,
            }

        return {
            "booking": booking,
            "totalAmount": 0,
            "commission": 0,
            "payoutAmount": 0,
            "walletResult": None,
        }
