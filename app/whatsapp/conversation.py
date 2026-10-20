import logging
from typing import Dict, Any, Optional, Union

from app.whatsapp.session import session_manager
from app.whatsapp.service import whatsapp_service
from app.whatsapp import messages
from app.whatsapp import ai_service
from app.repositories.connectify_repository import ConnectifyRepository

logger = logging.getLogger("connectify.conversation")


def get_user_name(user: Optional[Dict[str, Any]]) -> str:
    if not user:
        return ""
    return user.get("name") or user.get("full_name") or user.get("fullName") or ""


def get_user_id(user: Optional[Dict[str, Any]]) -> Optional[str]:
    if not user:
        return None
    return user.get("id") or user.get("_id")


def is_inactive(user: Optional[Dict[str, Any]]) -> bool:
    if not user:
        return False
    return (
        user.get("is_active") is False
        or user.get("isActive") is False
        or user.get("is_blocked") is True
        or user.get("isBlocked") is True
    )


def normalize_incoming(message: Any) -> Dict[str, Any]:
    if not message:
        return {"type": "text", "text": "", "actionId": None, "buttonId": None, "listId": None, "location": None}

    if isinstance(message, str):
        trimmed = message.strip()
        return {
            "type": "text",
            "text": trimmed,
            "actionId": trimmed,
            "buttonId": None,
            "listId": None,
            "location": None,
        }

    if isinstance(message, dict):
        return {
            "type": message.get("type", "text"),
            "text": str(message.get("text", "")).strip(),
            "actionId": message.get("buttonId") or message.get("listId") or message.get("actionId"),
            "buttonId": message.get("buttonId"),
            "listId": message.get("listId"),
            "location": message.get("location"),
        }

    return {"type": "text", "text": str(message).strip(), "actionId": None, "buttonId": None, "listId": None, "location": None}


def sanitize_session_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in updates.items() if k in ai_service.ALLOWED_SESSION_KEYS and v is not None}


def has_date_time(session: Dict[str, Any]) -> bool:
    return bool(session.get("date") and ai_service.normalize_time(session.get("time")))


class ConversationManager:
    def __init__(self, repository: Optional[ConnectifyRepository] = None):
        self.repository = repository or ConnectifyRepository()

    async def handle_incoming_message(self, phone_number: str, raw_message: Any) -> Dict[str, Any]:
        try:
            incoming = normalize_incoming(raw_message)
            trimmed = incoming["text"]
            action_id = incoming["actionId"]
            lower = trimmed.lower()

            # Handle quick suggestion button clicks (e.g. SUGG_Book_Plumber)
            if action_id and action_id.startswith("SUGG_"):
                if not trimmed or trimmed == action_id:
                    trimmed = action_id[5:].replace("_", " ")
                    lower = trimmed.lower()

            user = await self.repository.find_or_create_whatsapp_user(phone_number)

            if is_inactive(user):
                return await whatsapp_service.send_message(
                    phone_number, "Your account is currently restricted. Please contact support."
                )

            # Global Reset Command
            if lower in ["cancel", "exit", "restart", "reset"]:
                await session_manager.clear_session(phone_number)
                return await whatsapp_service.send_message(
                    phone_number, "Conversation reset. What service do you need today?"
                )

            # Handle GPS Location Pin Sharing
            if incoming.get("type") == "location" and incoming.get("location"):
                return await self.handle_location_pin(phone_number, user, incoming["location"])

            # Handle Role Selection Buttons
            if action_id == "ROLE_CUSTOMER":
                await self.repository.update_user_role(get_user_id(user), "customer")
                await session_manager.update_session(phone_number, {"step": "CUSTOMER_SETUP_LOCATION"})
                first_name = get_user_name(user).split(" ")[0]
                return await whatsapp_service.send_message(
                    phone_number, messages.customer_location_prompt(first_name)
                )

            if action_id == "ROLE_PROVIDER":
                await self.repository.update_user_role(get_user_id(user), "provider")
                await session_manager.update_session(phone_number, {"step": "PROVIDER_SETUP_CATEGORY"})
                list_menu = messages.provider_category_selection()
                return await whatsapp_service.send_interactive_list(
                    phone_number,
                    list_menu["text"],
                    list_menu["buttonLabel"],
                    list_menu["sections"],
                )

            # Handle Provider Trade Selection
            if action_id and action_id.startswith("TRADE_"):
                trade_map = {
                    "TRADE_PLUMBING": "Plumbing",
                    "TRADE_ELECTRICAL": "Electrical",
                    "TRADE_AC": "AC & Refrigeration",
                    "TRADE_GENERATOR": "Generator Repair",
                    "TRADE_CLEANING": "Home & Office Clean",
                    "TRADE_CARPENTRY": "Carpentry",
                    "TRADE_PAINTING": "Painting",
                }
                category = trade_map.get(action_id, "General Service")
                await session_manager.update_session(
                    phone_number, {"step": "PROVIDER_SETUP_LOCATION", "providerCategory": category}
                )
                return await whatsapp_service.send_message(
                    phone_number, messages.provider_location_prompt(category)
                )

            # Provider Booking Action Buttons: ACCEPT_BOOKING_<id>, DECLINE_BOOKING_<id>
            if action_id and action_id.startswith("ACCEPT_BOOKING_"):
                booking_id = action_id.replace("ACCEPT_BOOKING_", "")
                return await self.handle_provider_accept_booking(phone_number, user, booking_id)

            if action_id and action_id.startswith("DECLINE_BOOKING_"):
                booking_id = action_id.replace("DECLINE_BOOKING_", "")
                return await self.handle_provider_decline_booking(phone_number, user, booking_id)

            # Escrow Release and Rating: RATE_5_<id>, RATE_4_<id>, COMPLETE_BOOKING_<id>
            if action_id and (
                action_id.startswith("RATE_5_")
                or action_id.startswith("RATE_4_")
                or action_id.startswith("COMPLETE_BOOKING_")
            ):
                booking_id = action_id.replace("RATE_5_", "").replace("RATE_4_", "").replace("COMPLETE_BOOKING_", "")
                return await self.handle_complete_booking_and_escrow(phone_number, user, booking_id)

            # Dispute & Emergency Detection
            dispute_keywords = [
                "scam", "police", "stole", "stolen", "fraud", "damaged",
                "broken my", "ruined my", "refund", "dispute", "cheat", "thief"
            ]
            if (any(kw in lower for kw in dispute_keywords) and not any(f in lower for f in ["how do i pay", "is it safe", "escrow"])) or (action_id and action_id.startswith("DISPUTE_")):
                import uuid
                ticket_id = f"DISP-{uuid.uuid4().hex[:6].upper()}"
                return await whatsapp_service.send_message(phone_number, messages.dispute_alert(ticket_id))

            # Global Menu Command
            if lower in ["menu", "options", "home"] or action_id == "CMD_MENU":
                return await self.send_main_menu(phone_number, user)

            # Wallet Command
            if lower in ["wallet", "balance"] or action_id == "CMD_WALLET":
                wallet = await self.repository.get_user_wallet(get_user_id(user))
                return await whatsapp_service.send_message(
                    phone_number, messages.wallet_summary(wallet, user.get("role", "customer"))
                )

            # Bookings / Jobs Command
            if lower in ["bookings", "jobs", "requests"] or action_id in [
                "CMD_MY_BOOKINGS", "CMD_ACTIVE_JOBS", "CMD_PENDING_JOBS"
            ]:
                return await self.handle_bookings_list(phone_number, user, action_id)

            # Find Service Command & Category Selection
            if action_id == "CMD_FIND_SERVICE" or lower in [
                "find a service", "find service", "services", "categories", "browse services", "trades"
            ]:
                await session_manager.update_session(phone_number, {"step": "CATEGORY_SELECTION"})
                cat_menu = messages.customer_category_selection()
                return await whatsapp_service.send_interactive_list(
                    phone_number,
                    cat_menu["text"],
                    cat_menu["buttonLabel"],
                    cat_menu["sections"],
                )

            # Customer Category Picker Selection: CATEGORY_PLUMBING, etc.
            category_map = {
                "CATEGORY_PLUMBING": "Plumbing",
                "CATEGORY_ELECTRICAL": "Electrical",
                "CATEGORY_AC": "AC & Refrigeration",
                "CATEGORY_GENERATOR": "Generator Repair",
                "CATEGORY_CLEANING": "Home & Office Clean",
                "CATEGORY_CARPENTRY": "Carpentry",
                "CATEGORY_PAINTING": "Painting",
            }
            if action_id and action_id in category_map:
                category_name = category_map[action_id]
                await session_manager.update_session(
                    phone_number, {"step": "AWAITING_LOCATION", "service": category_name}
                )
                return await whatsapp_service.send_message(
                    phone_number, messages.location_prompt_for_category(category_name)
                )

            session = await session_manager.get_session(phone_number)
            has_name = bool(get_user_name(user))
            user_profile = user.get("profile") or {}
            is_onboarded = bool(user_profile.get("onboarded", False) and has_name)

            # Greetings / Help
            if lower in ["hi", "hello", "hey", "help", "start"]:
                if is_onboarded:
                    return await self.send_main_menu(phone_number, user)
                await session_manager.update_session(phone_number, {"step": "ONBOARDING"})
                return await whatsapp_service.send_message(phone_number, messages.welcome())

            # Onboarding Step: Collect user's full name
            if not has_name or session.get("step") == "ONBOARDING":
                if len(trimmed) < 2:
                    await session_manager.update_session(phone_number, {"step": "ONBOARDING"})
                    return await whatsapp_service.send_message(phone_number, messages.welcome())

                updated_user = await self.repository.update_user_name(get_user_id(user), trimmed)
                first_name = get_user_name(updated_user).split(" ")[0] or trimmed.split(" ")[0]

                # Offer role selection interactive buttons
                role_menu = messages.role_selection(first_name)
                return await whatsapp_service.send_interactive_buttons(
                    phone_number, role_menu["text"], role_menu["buttons"]
                )

            # Customer Onboarding: Location Setup
            if session.get("step") == "CUSTOMER_SETUP_LOCATION":
                loc = trimmed.title() if trimmed else "Lagos"
                await self.repository.update_user_location(get_user_id(user), label=loc)
                await session_manager.update_session(phone_number, {"step": "READY", "location": loc})
                first_name = get_user_name(user).split(" ")[0]
                welcome_card = messages.customer_onboarding_completed(first_name, loc)
                return await whatsapp_service.send_interactive_buttons(
                    phone_number, welcome_card["text"], welcome_card["buttons"]
                )

            # Provider Onboarding: Coverage Area Setup
            if session.get("step") == "PROVIDER_SETUP_LOCATION":
                category = session.get("providerCategory") or "General Service"
                prov_loc = trimmed.title() if trimmed else "Lagos"
                await self.repository.update_user_location(get_user_id(user), label=prov_loc)
                await session_manager.update_session(
                    phone_number, {"step": "PROVIDER_SETUP_PRICE", "providerLocation": prov_loc}
                )
                return await whatsapp_service.send_message(
                    phone_number, messages.provider_price_prompt(category)
                )

            # Provider Setup Step: Setting their rate/price
            if session.get("step") == "PROVIDER_SETUP_PRICE":
                cleaned_price = "".join(ch for ch in trimmed if ch.isdigit())
                numeric_price = float(cleaned_price) if cleaned_price else 5000
                price = numeric_price if numeric_price >= 500 else 5000
                category = session.get("providerCategory") or "General Service"
                prov_loc = session.get("providerLocation") or "Lagos, Nigeria"

                await self.repository.create_provider_service(
                    provider_id=get_user_id(user),
                    name=f"{category} Service",
                    category=category,
                    price=price,
                    location={"address": prov_loc, "city": prov_loc.split(",")[0].strip()},
                )

                await session_manager.update_session(
                    phone_number, {"step": "READY", "providerCategory": None, "providerLocation": None}
                )
                await whatsapp_service.send_message(
                    phone_number, messages.provider_service_created(category, price)
                )
                return await self.send_main_menu(phone_number, user)

            # Customer Awaiting Location Step during search
            if session.get("step") == "AWAITING_LOCATION":
                svc = session.get("service") or "service"
                loc = trimmed.title()
                await session_manager.update_session(
                    phone_number, {"step": "AWAITING_DATETIME", "location": loc}
                )
                return await whatsapp_service.send_message(
                    phone_number,
                    f"📍 Location set to *{loc}* for *{svc}*.\n\n"
                    'When do you need the service? (e.g. *"tomorrow 10am"* or *"today 3pm"*)',
                )

            # Provider Selection Step
            if session.get("step") == "PROVIDER_SELECTION":
                return await self.handle_provider_selection(phone_number, trimmed, user, session)

            # Awaiting Confirmation Step
            if session.get("step") == "AWAITING_CONFIRMATION":
                return await self.handle_confirmation_response(phone_number, trimmed, session, action_id)

            # Awaiting Date/Time Step
            if session.get("step") == "AWAITING_DATETIME":
                return await self.handle_date_time_capture(phone_number, trimmed, session)

            # Provider Menu commands when user is a provider
            if user.get("role") == "provider" and not session.get("service"):
                return await self.send_main_menu(phone_number, user)

            # Default: Customer Service Search using Gemini AI or Local NLP
            return await self.handle_service_search(phone_number, trimmed, session)

        except Exception as error:
            logger.error(f"[WhatsAppConversation] Error: {error}", exc_info=True)
            return await whatsapp_service.send_message(
                phone_number,
                "Sorry, something went wrong while processing that. Please try again in a moment.",
            )

    async def send_main_menu(self, phone_number: str, user: Dict[str, Any]) -> Dict[str, Any]:
        first_name = get_user_name(user).split(" ")[0] or "Friend"
        if user.get("role") == "provider":
            pending = await self.repository.get_provider_pending_bookings(get_user_id(user))
            menu = messages.provider_main_menu(first_name, len(pending))
            return await whatsapp_service.send_interactive_buttons(
                phone_number, menu["text"], menu["buttons"]
            )

        menu = messages.customer_main_menu(first_name)
        return await whatsapp_service.send_interactive_buttons(
            phone_number, menu["text"], menu["buttons"]
        )

    async def handle_location_pin(
        self, phone_number: str, user: Dict[str, Any], location: Dict[str, Any]
    ) -> Dict[str, Any]:
        lat = location.get("latitude")
        lng = location.get("longitude")
        location_label = (
            location.get("name")
            or location.get("address")
            or f"{lat:.4f}, {lng:.4f}"
        )
        await self.repository.update_user_location(
            get_user_id(user),
            label=location_label,
            coordinates={"latitude": lat, "longitude": lng},
        )

        session = await session_manager.get_session(phone_number)
        step = session.get("step")

        # 1. Customer onboarding step
        if step == "CUSTOMER_SETUP_LOCATION":
            await session_manager.update_session(phone_number, {"step": "READY", "location": location_label})
            first_name = get_user_name(user).split(" ")[0]
            welcome_card = messages.customer_onboarding_completed(first_name, location_label)
            return await whatsapp_service.send_interactive_buttons(
                phone_number, welcome_card["text"], welcome_card["buttons"]
            )

        # 2. Provider onboarding step
        if step == "PROVIDER_SETUP_LOCATION":
            category = session.get("providerCategory") or "General Service"
            await session_manager.update_session(
                phone_number, {"step": "PROVIDER_SETUP_PRICE", "providerLocation": location_label}
            )
            return await whatsapp_service.send_message(
                phone_number, messages.provider_price_prompt(category)
            )

        svc = session.get("service")
        await session_manager.update_session(phone_number, {"location": location_label})

        if svc:
            if session.get("date") and session.get("time"):
                return await self.find_and_present_providers(
                    phone_number, {**session, "location": location_label}
                )
            await session_manager.update_session(
                phone_number, {"step": "AWAITING_DATETIME", "location": location_label}
            )
            return await whatsapp_service.send_message(
                phone_number,
                f"📍 Location saved: *{location_label}* for *{svc}*.\n\n"
                'When do you need the service? (e.g. *"tomorrow 10am"* or *"today 3pm"*)',
            )

        return await whatsapp_service.send_message(
            phone_number,
            f"📍 Location saved: *{location_label}*.\n\n"
            'What service do you need here today? (e.g. "plumber", "cleaner", "electrician")',
        )

    async def handle_bookings_list(
        self, phone_number: str, user: Dict[str, Any], action_id: Optional[str]
    ) -> Dict[str, Any]:
        user_id = get_user_id(user)
        if user.get("role") == "provider":
            bookings = (
                await self.repository.get_provider_active_bookings(user_id)
                if action_id == "CMD_ACTIVE_JOBS"
                else await self.repository.get_provider_pending_bookings(user_id)
            )
            return await whatsapp_service.send_message(
                phone_number, messages.booking_list(bookings, "provider")
            )

        bookings = await self.repository.get_customer_bookings(user_id)
        return await whatsapp_service.send_message(
            phone_number, messages.booking_list(bookings, "customer")
        )

    async def handle_provider_accept_booking(
        self, phone_number: str, user: Dict[str, Any], booking_id: str
    ) -> Dict[str, Any]:
        try:
            booking = await self.repository.get_booking_by_id(booking_id)
            if not booking:
                return await whatsapp_service.send_message(phone_number, "Booking not found.")

            actor_id = get_user_id(user)
            prov_id = booking.get("provider_id") or (booking.get("provider") or {}).get("id")
            if str(actor_id) != str(prov_id):
                return await whatsapp_service.send_message(
                    phone_number, "⚠️ You are not authorized to accept this booking."
                )

            updated_booking = await self.repository.update_booking_status_atomic(
                booking_id, "confirmed", actor_id
            )
            svc_name = (updated_booking.get("service") or {}).get("name") if isinstance(updated_booking.get("service"), dict) else "Service"
            await whatsapp_service.send_message(
                phone_number,
                f"✅ You have *ACCEPTED* the job request for {svc_name} on {updated_booking.get('date')}!",
            )

            # Instantly notify customer via WhatsApp
            customer = updated_booking.get("customer") or {}
            if customer.get("phone"):
                await whatsapp_service.send_message(
                    customer["phone"],
                    messages.customer_booking_confirmed(updated_booking, user),
                )
            return {"status": "confirmed"}
        except Exception as err:
            logger.error(f"[WhatsAppConversation] Error accepting booking: {err}")
            return await whatsapp_service.send_message(
                phone_number, "Failed to update booking status. It may have already been cancelled."
            )

    async def handle_provider_decline_booking(
        self, phone_number: str, user: Dict[str, Any], booking_id: str
    ) -> Dict[str, Any]:
        try:
            booking = await self.repository.get_booking_by_id(booking_id)
            if not booking:
                return await whatsapp_service.send_message(phone_number, "Booking not found.")

            actor_id = get_user_id(user)
            prov_id = booking.get("provider_id") or (booking.get("provider") or {}).get("id")
            if str(actor_id) != str(prov_id):
                return await whatsapp_service.send_message(
                    phone_number, "⚠️ You are not authorized to decline this booking."
                )

            updated_booking = await self.repository.update_booking_status_atomic(
                booking_id, "cancelled", actor_id
            )
            await whatsapp_service.send_message(phone_number, "You have declined this job request.")

            # Instantly notify customer via WhatsApp
            customer = updated_booking.get("customer") or {}
            if customer.get("phone"):
                await whatsapp_service.send_message(
                    customer["phone"],
                    messages.customer_booking_declined(updated_booking, user),
                )
            return {"status": "cancelled"}
        except Exception as err:
            logger.error(f"[WhatsAppConversation] Error declining booking: {err}")
            return await whatsapp_service.send_message(phone_number, "Failed to update booking status.")

    async def handle_service_search(
        self, phone_number: str, message: str, session: Dict[str, Any]
    ) -> Dict[str, Any]:
        current_calls = int(session.get("aiCalls") or 0)
        ai_response = await ai_service.analyze_intent(message, session)

        if ai_response.get("rateLimited"):
            await session_manager.update_session(phone_number, {"step": "READY"})
            return await whatsapp_service.send_message(
                phone_number,
                'I think we got a bit stuck there. Could you tell me the service you need and your area, e.g. "a plumber in Ikeja"?',
            )

        next_updates = {
            **sanitize_session_updates(ai_response.get("sessionUpdates") or {}),
            "aiCalls": current_calls + 1,
        }
        next_session = await session_manager.update_session(phone_number, next_updates)

        if not next_session.get("isConfirmed"):
            suggestions = ai_response.get("suggestions") or []
            if suggestions and isinstance(suggestions, list):
                buttons = [
                    {"id": f"SUGG_{s.strip()[:15].replace(' ', '_')}", "title": s.strip()[:20]}
                    for s in suggestions
                    if s and s.strip()
                ][:3]
                if buttons:
                    return await whatsapp_service.send_interactive_buttons(
                        phone_number, ai_response.get("replyText"), buttons
                    )
            return await whatsapp_service.send_message(phone_number, ai_response.get("replyText"))

        await session_manager.update_session(phone_number, {"step": "AWAITING_CONFIRMATION"})
        confirm_buttons = [
            {"id": "CONFIRM_YES", "title": "Yes, Find Pros"},
            {"id": "CMD_MENU", "title": "Main Menu"},
        ]
        return await whatsapp_service.send_interactive_buttons(
            phone_number, messages.confirm_summary(next_session), confirm_buttons
        )

    async def handle_confirmation_response(
        self,
        phone_number: str,
        message: str,
        session: Dict[str, Any],
        action_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        text = message.lower().strip()
        if text in ["yes", "yup", "correct", "confirm", "yes, find pros"] or action_id == "CONFIRM_YES":
            if not ai_service.normalize_date(session.get("date")) or not ai_service.normalize_time(session.get("time")):
                await session_manager.update_session(phone_number, {"step": "AWAITING_DATETIME"})
                return await whatsapp_service.send_message(phone_number, messages.ask_date_time())

            await whatsapp_service.send_message(
                phone_number,
                f"Searching for verified {session.get('service')} providers near {session.get('location')}...",
            )
            return await self.find_and_present_providers(phone_number, session)

        await session_manager.clear_session(phone_number)
        return await whatsapp_service.send_message(
            phone_number, "No problem! Let's start fresh. What service do you need today?"
        )

    async def handle_date_time_capture(
        self, phone_number: str, message: str, session: Dict[str, Any]
    ) -> Dict[str, Any]:
        date = ai_service.normalize_date(message) or session.get("date")
        time = ai_service.normalize_time(message) or session.get("time")

        updates: Dict[str, Any] = {}
        if date:
            updates["date"] = date
        if time:
            updates["time"] = time

        updated = await session_manager.update_session(phone_number, updates)

        if updated.get("date") and ai_service.normalize_time(updated.get("time")):
            return await self.find_and_present_providers(phone_number, updated)

        missing = []
        if not ai_service.normalize_date(updated.get("date")):
            missing.append("date")
        if not ai_service.normalize_time(updated.get("time")):
            missing.append("time")
        return await whatsapp_service.send_message(
            phone_number,
            f"Almost there. Could you tell me the {' and '.join(missing)}? e.g. \"tomorrow 5pm\".",
        )

    async def find_and_present_providers(
        self, phone_number: str, session: Dict[str, Any]
    ) -> Dict[str, Any]:
        providers = await self.repository.find_matching_services(
            service=session.get("service"),
            location=session.get("location"),
            limit=3,
        )

        if not providers:
            await session_manager.update_session(
                phone_number, {"step": "READY", "isConfirmed": False, "matchedProviders": []}
            )
            return await whatsapp_service.send_message(
                phone_number, messages.no_providers(session.get("service"), session.get("location"))
            )

        await session_manager.update_session(
            phone_number, {"step": "PROVIDER_SELECTION", "matchedProviders": providers}
        )
        return await whatsapp_service.send_message(
            phone_number, messages.provider_list(providers)
        )

    async def handle_provider_selection(
        self, phone_number: str, message: str, user: Dict[str, Any], session: Dict[str, Any]
    ) -> Dict[str, Any]:
        providers = session.get("matchedProviders") or []
        try:
            choice_index = int(message.strip()) - 1
            if choice_index < 0 or choice_index >= len(providers):
                return await whatsapp_service.send_message(
                    phone_number, "Please reply with a number from the list."
                )
        except ValueError:
            return await whatsapp_service.send_message(
                phone_number, "Please reply with a number from the list."
            )

        if not has_date_time(session):
            await session_manager.update_session(phone_number, {"step": "AWAITING_DATETIME"})
            return await whatsapp_service.send_message(phone_number, messages.ask_date_time())

        selected_provider = providers[choice_index]

        try:
            booking = await self.repository.create_booking_request(
                customer_id=get_user_id(user),
                provider=selected_provider,
                session=session,
            )

            await session_manager.clear_session(phone_number)

            # Instantly dispatch interactive alert to the provider via WhatsApp
            prov_phone = (booking.get("provider") or {}).get("phone")
            if prov_phone:
                alert = messages.provider_new_booking_alert(booking)
                await whatsapp_service.send_interactive_buttons(
                    prov_phone, alert["text"], alert["buttons"]
                )

            return await whatsapp_service.send_message(
                phone_number, messages.booking_created(selected_provider, booking)
            )

        except Exception as error:
            logger.error(f"[WhatsAppConversation] Booking error: {error}")
            err_str = str(error)
            if "already booked" in err_str.lower() or "time slot" in err_str.lower():
                return await whatsapp_service.send_message(
                    phone_number,
                    f"The {session.get('time')} slot on {session.get('date')} is unfortunately already taken. "
                    'Reply with a different time (e.g. "5pm") and I will try again.',
                )
            await session_manager.update_session(phone_number, {"step": "AWAITING_DATETIME"})
            return await whatsapp_service.send_message(
                phone_number, messages.booking_failed(session)
            )

    async def handle_complete_booking_and_escrow(
        self, phone_number: str, user: Dict[str, Any], booking_id: str
    ) -> Dict[str, Any]:
        try:
            booking = await self.repository.get_booking_by_id(booking_id)
            if not booking:
                return await whatsapp_service.send_message(
                    phone_number, "Booking not found or already completed."
                )

            # Security: Only the customer who placed the booking can release escrow / rate completion
            actor_id = get_user_id(user)
            customer = booking.get("customer") or {}
            cust_id = booking.get("customer_id") or customer.get("id")
            if str(actor_id) != str(cust_id):
                logger.warning(
                    f"[WhatsAppConversation] Unauthorized escrow release attempt for booking {booking_id} by actor {actor_id}"
                )
                return await whatsapp_service.send_message(
                    phone_number,
                    "⚠️ Only the customer who booked this service can confirm job completion and release payment.",
                )

            payout = await self.repository.complete_booking_with_escrow_payout(
                booking_id=booking["id"],
                provider_id=booking["provider_id"],
                actor_id=actor_id,
            )

            await whatsapp_service.send_message(
                phone_number,
                "⭐ Thank you! Your rating and completion have been recorded, and payment has been released to the artisan.",
            )

            prov_phone = (booking.get("provider") or {}).get("phone")
            if prov_phone:
                await whatsapp_service.send_message(
                    prov_phone,
                    messages.escrow_payout_notice(payout["payoutAmount"], payout["commission"]),
                )
            return payout
        except Exception as err:
            logger.error(f"[WhatsAppConversation] Error completing escrow booking: {err}")
            return await whatsapp_service.send_message(
                phone_number, "Failed to finalize job completion."
            )


conversation_manager = ConversationManager()
