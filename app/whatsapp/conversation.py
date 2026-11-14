import logging
import re
from typing import Dict, Any, Optional, Union

from app.whatsapp.session import session_manager
from app.whatsapp.service import whatsapp_service
from app.whatsapp import messages
from app.whatsapp import ai_service
from app.whatsapp.memory import reply_capture, history_tail
from app.whatsapp.user_memory import get_memory, prompt_memory, reusable_request, describe_memory
from app.whatsapp.booking_details import BOOKING_STEPS, has_draft, location_label, schedule_updates, schedule_problem, needs_interpretation, is_question
from app.repositories.connectify_repository import ConnectifyRepository

logger = logging.getLogger("connectify.conversation")
GREETINGS = {"hi", "hello", "hey", "help", "start", "hi there", "hello there",
             "good morning", "good afternoon", "good evening"}
CONFIRMATIONS = {"yes", "yup", "correct", "confirm", "yes, find pros", "confirm & find pros",
                 "looks good", "that's right", "that is correct", "go ahead", "yes please", "proceed"}


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
    return bool(ai_service.normalize_date(session.get("date")) and ai_service.normalize_time(session.get("time")))


class ConversationManager:
    def __init__(self, repository: Optional[ConnectifyRepository] = None):
        self.repository = repository or ConnectifyRepository()

    async def handle_incoming_message(self, phone_number: str, raw_message: Any) -> Dict[str, Any]:
        incoming = normalize_incoming(raw_message)
        before = await session_manager.get_session(phone_number)
        history = history_tail(before.get("history") or [])
        text = incoming["text"] or ("Shared a location pin" if incoming.get("location") else incoming.get("actionId") or "")
        history = history_tail(history + [{"role": "user", "text": text}])
        await session_manager.update_session(phone_number, {"history": history})
        capture = {"phone": str(phone_number).lstrip("+"), "replies": []}
        token = reply_capture.set(capture)
        try:
            return await self._handle_message(phone_number, raw_message)
        finally:
            reply_capture.reset(token)
            after = await session_manager.get_session(phone_number)
            # Explicit reset/booked sessions must not resurrect the previous draft/history.
            history = history_tail(after.get("history") or [])
            await session_manager.update_session(phone_number, {"history": history_tail(history + capture["replies"])})

    async def _handle_message(self, phone_number: str, raw_message: Any) -> Dict[str, Any]:
        try:
            incoming = normalize_incoming(raw_message)
            trimmed = incoming["text"]
            action_id = incoming["actionId"]
            lower = trimmed.lower()
            is_greeting = lower.strip("!.? ") in GREETINGS

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

            memory = get_memory(user)
            if (is_greeting or action_id == "MEMORY_REUSE" or lower in {"memory", "my preferences", "what do you remember", "what do you remember about me", "what do you remember about me?", "same as last time", "same again", "use my last request", "book again"}) and get_user_name(user):
                memory = await self.hydrate_usage_memory(user)
            await session_manager.update_session(phone_number, {"usageMemory": prompt_memory(memory)})
            if lower in {"memory", "my preferences", "what do you remember", "what do you remember about me", "what do you remember about me?"}:
                return await whatsapp_service.send_message(phone_number, describe_memory(user))
            if lower in {"memory on", "memory off", "forget my preferences"}:
                try:
                    changed = await self.repository.update_usage_memory(
                        user, enabled=(lower == "memory on"), forget=(lower == "forget my preferences"),
                    )
                    await session_manager.update_session(phone_number, {"usageMemory": prompt_memory(changed)})
                except Exception:
                    logger.warning("Could not update usage-memory preferences")
                    return await whatsapp_service.send_message(phone_number, "I couldn't update your saved preferences right now. Please try again.")
                if lower == "forget my preferences":
                    return await whatsapp_service.send_message(phone_number, "I've cleared your saved service and location preferences and turned personalization off. Your account and booking records are unchanged. Say *memory on* to enable it again.")
                return await whatsapp_service.send_message(phone_number, "Personalized memory is on. I'll remember confirmed service requests for future visits." if changed["enabled"] else "Personalized memory is off. I won't use or update saved service preferences. Say *forget my preferences* if you'd like to delete them too.")
            if action_id == "MEMORY_REUSE" or lower in {"same as last time", "same again", "use my last request", "book again"}:
                previous = reusable_request(memory)
                if not previous or not previous.get("service") or not previous.get("location"):
                    return await whatsapp_service.send_message(phone_number, "I don't have a saved service request to reuse. What service and area do you need?")
                restored = await session_manager.update_session(phone_number, {
                    "service": previous["service"], "location": previous["location"], "date": None, "time": None,
                    "timePeriod": None, "budget": None, "urgency": "normal", "matchedProviders": [],
                    "isConfirmed": False, "step": "AWAITING_DATETIME", "wasenderChoices": [],
                    "pendingLocation": None, "editingService": False,
                })
                return await self.advance_booking(phone_number, restored)

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
            if lower in ["menu", "main menu", "options", "home"] or action_id == "CMD_MENU":
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
                await session_manager.update_session(phone_number, {
                    "step": "CATEGORY_SELECTION", "service": None, "location": None,
                    "date": None, "time": None, "matchedProviders": [], "isConfirmed": False,
                    "pendingLocation": None, "editingService": False, "aiCalls": 0,
                    "wasenderChoices": [],
                })
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
                current = await session_manager.get_session(phone_number)
                if current.get("editingService"):
                    updated = await session_manager.update_session(phone_number, {
                        "service": category_name, "editingService": False, "matchedProviders": [],
                    })
                    return await self.advance_booking(phone_number, updated)
                await session_manager.update_session(phone_number, {
                    "step": "AWAITING_LOCATION", "service": category_name, "location": None,
                    "date": None, "time": None, "matchedProviders": [], "isConfirmed": False,
                    "wasenderChoices": [],
                })
                return await whatsapp_service.send_message(
                    phone_number, messages.location_prompt_for_category(category_name)
                )

            session = await session_manager.get_session(phone_number)
            has_name = bool(get_user_name(user))
            user_profile = user.get("profile") or {}
            is_onboarded = bool((user_profile.get("onboarded", False) or memory.get("lastBooking")) and has_name)

            if session.get("step") == "PROVIDER_SELECTION" and not is_question(trimmed) and re.search(r"\b(first|second|third|(?:number|option|provider)\s*[1-3])\b", lower):
                return await self.handle_provider_selection(phone_number, trimmed, user, session)

            if has_name and not is_greeting and lower not in CONFIRMATIONS | {"change details", "edit", "change", "no", "continue", "resume"} and (not action_id or action_id == trimmed):
                if is_question(trimmed) or (has_draft(session) and needs_interpretation(trimmed, session)):
                    return await self.handle_natural_message(phone_number, trimmed, session, user)

            if has_draft(session):
                if action_id in {"EDIT_SERVICE", "EDIT_LOCATION", "EDIT_DATE", "EDIT_TIME"}:
                    return await self.edit_booking(phone_number, session, action_id)
                if action_id == "CMD_EDIT_BOOKING" or lower in {"change details", "edit", "change"}:
                    menu = messages.edit_booking_menu()
                    await session_manager.update_session(phone_number, {"step": "EDIT_BOOKING"})
                    return await whatsapp_service.send_interactive_list(phone_number, menu["text"], menu["buttonLabel"], menu["sections"])
                if is_greeting or lower in {"resume", "continue"}:
                    return await self.resume_booking(phone_number, session)
                if session.get("step") == "CONFIRM_LOCATION":
                    if lower in {"yes", "correct", "confirm"} or action_id == "LOCATION_YES":
                        return await self.save_booking_location(phone_number, session["pendingLocation"])
                    if lower in {"no", "change location"} or action_id == "LOCATION_NO":
                        await session_manager.update_session(phone_number, {"step": "AWAITING_LOCATION", "pendingLocation": None})
                        return await whatsapp_service.send_message(phone_number, messages.location_prompt_for_category(session["service"]))
                    return await self.capture_booking_location(phone_number, trimmed)
                if session.get("step") == "AWAITING_LOCATION":
                    return await self.capture_booking_location(phone_number, trimmed)
                if session.get("step") == "AWAITING_DATETIME":
                    return await self.handle_date_time_capture(phone_number, trimmed, session)
                if session.get("step") == "AWAITING_CONFIRMATION":
                    return await self.handle_confirmation_response(phone_number, trimmed, session, action_id, user)
                if session.get("step") == "EDIT_BOOKING":
                    field_actions = {"service": "EDIT_SERVICE", "location": "EDIT_LOCATION", "date": "EDIT_DATE", "time": "EDIT_TIME"}
                    if lower in field_actions:
                        return await self.edit_booking(phone_number, session, field_actions[lower])
                    if schedule_updates(trimmed, session):
                        return await self.handle_date_time_capture(phone_number, trimmed, session)
                    menu = messages.edit_booking_menu()
                    return await whatsapp_service.send_interactive_list(phone_number, menu["text"], menu["buttonLabel"], menu["sections"])
                # Recover a complete/partial draft whose step was overwritten by
                # older application code. A date reply must never go back to AI.
                if session.get("step") in {"init", "READY", "ONBOARDING", "MAIN_MENU"}:
                    if session.get("location"):
                        return await self.handle_date_time_capture(phone_number, trimmed, session)
                    return await self.capture_booking_location(phone_number, trimmed)

            # Greetings / Help
            if is_greeting:
                if has_name:
                    if session.get("step") == "CUSTOMER_SETUP_LOCATION":
                        return await whatsapp_service.send_message(phone_number, messages.customer_location_prompt(get_user_name(user)))
                    if session.get("step") == "PROVIDER_SETUP_LOCATION":
                        return await whatsapp_service.send_message(phone_number, messages.provider_location_prompt(session.get("providerCategory")))
                    if session.get("step") == "PROVIDER_SETUP_PRICE":
                        return await whatsapp_service.send_message(phone_number, messages.provider_price_prompt(session.get("providerCategory")))
                    if session.get("step") == "PROVIDER_SETUP_CATEGORY":
                        menu = messages.provider_category_selection()
                        return await whatsapp_service.send_interactive_list(phone_number, menu["text"], menu["buttonLabel"], menu["sections"])
                    if is_onboarded:
                        return await self.send_main_menu(phone_number, user)
                    role_menu = messages.role_selection(get_user_name(user).split()[0])
                    await session_manager.update_session(phone_number, {"step": "ROLE_SELECTION"})
                    return await whatsapp_service.send_interactive_buttons(phone_number, role_menu["text"], role_menu["buttons"])
                await session_manager.update_session(phone_number, {"step": "ONBOARDING"})
                return await whatsapp_service.send_message(phone_number, messages.welcome())

            # Onboarding Step: Collect user's full name
            if not has_name or session.get("step") == "ONBOARDING":
                if not has_name and re.search(r"\b(need|looking for|book|want|find)\b", lower):
                    await session_manager.update_session(phone_number, {"step": "ONBOARDING", "pendingRequest": trimmed})
                    return await whatsapp_service.send_message(phone_number, "I can help with that. First, what is your full name?")
                if len(trimmed) < 2:
                    await session_manager.update_session(phone_number, {"step": "ONBOARDING"})
                    return await whatsapp_service.send_message(phone_number, messages.welcome())

                updated_user = await self.repository.update_user_name(get_user_id(user), trimmed)
                await session_manager.update_session(phone_number, {"step": "ROLE_SELECTION"})
                first_name = get_user_name(updated_user).split(" ")[0] or trimmed.split(" ")[0]
                if session.get("pendingRequest"):
                    updated_user = await self.repository.update_user_role(get_user_id(user), "customer")
                    await self.repository.mark_onboarded(updated_user)
                    restored = await session_manager.update_session(phone_number, {"pendingRequest": None, "step": "READY"})
                    return await self.handle_natural_message(phone_number, session["pendingRequest"], restored, updated_user)

                # Offer role selection interactive buttons
                role_menu = messages.role_selection(first_name)
                return await whatsapp_service.send_interactive_buttons(
                    phone_number, role_menu["text"], role_menu["buttons"]
                )

            # Customer Onboarding: Location Setup
            if session.get("step") == "CUSTOMER_SETUP_LOCATION":
                loc, _ = location_label(trimmed)
                if not loc:
                    return await whatsapp_service.send_message(phone_number, messages.customer_location_prompt(get_user_name(user)))
                updated_user = await self.repository.update_user_location(get_user_id(user), label=loc)
                await self.repository.mark_onboarded(updated_user)
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
                await self.repository.mark_onboarded(user)

                await session_manager.update_session(
                    phone_number, {"step": "READY", "providerCategory": None, "providerLocation": None}
                )
                await whatsapp_service.send_message(
                    phone_number, messages.provider_service_created(category, price)
                )
                return await self.send_main_menu(phone_number, user)

            # Provider Selection Step
            if session.get("step") == "PROVIDER_SELECTION":
                return await self.handle_provider_selection(phone_number, trimmed, user, session)

            # Awaiting Confirmation Step
            if session.get("step") == "AWAITING_CONFIRMATION":
                return await self.handle_confirmation_response(phone_number, trimmed, session, action_id, user)

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

    async def advance_booking(self, phone_number, session):
        if not session.get("location"):
            await session_manager.update_session(phone_number, {"step": "AWAITING_LOCATION", "wasenderChoices": []})
            return await whatsapp_service.send_message(phone_number, messages.location_prompt_for_category(session.get("service") or "service"))
        if not has_date_time(session):
            await session_manager.update_session(phone_number, {"step": "AWAITING_DATETIME", "wasenderChoices": []})
            return await whatsapp_service.send_message(phone_number, messages.ask_date_time(session))
        problem = schedule_problem(session)
        if problem:
            await session_manager.update_session(phone_number, {"step": "AWAITING_DATETIME", "wasenderChoices": []})
            return await whatsapp_service.send_message(phone_number, problem)
        await session_manager.update_session(phone_number, {"step": "AWAITING_CONFIRMATION", "isConfirmed": True})
        return await whatsapp_service.send_interactive_buttons(phone_number, messages.confirm_summary(session), [
            {"id": "CONFIRM_YES", "title": "Confirm & find pros"},
            {"id": "CMD_EDIT_BOOKING", "title": "Change details"},
        ])

    async def handle_natural_message(self, phone_number, text, session, user=None):
        response = await ai_service.analyze_intent(text, {**session, "role": (user or {}).get("role", "customer")})
        updates = sanitize_session_updates(response.get("sessionUpdates") or {})
        # AI cannot establish user consent, wallet state, or arbitrary FSM steps.
        updates.pop("isConfirmed", None)
        exact = schedule_updates(text, session)
        if re.search(r"\bsame (place|location|area)\b", text, re.I):
            updates.pop("location", None)
        if re.search(r"\bsame (service|trade)\b", text, re.I):
            updates.pop("service", None)
        if response.get("intent") == "CONVERSATION_OR_FAQ" or (is_question(text) and response.get("source") == "local" and not exact and not re.search(r"\b(change|move|switch|book|need|want|reschedule)\b", text, re.I)):
            return await whatsapp_service.send_message(phone_number, response.get("replyText") or "What would you like to know?")
        for field in response.get("clearFields") or []:
            if field in {"date", "time"}:
                updates[field] = None
        if response.get("timePeriod") and not updates.get("time"):
            updates.update({"timePeriod": response["timePeriod"], "time": None})
        updates.update(exact)
        if updates.get("time"):
            updates["timePeriod"] = None
        if updates.get("service") or updates.get("location") or updates.get("date") or "time" in updates:
            updates["matchedProviders"] = []
        updated = await session_manager.update_session(phone_number, {
            **updates, "aiCalls": int(session.get("aiCalls") or 0) + 1,
        })
        if not updated.get("service"):
            return await whatsapp_service.send_message(phone_number, response.get("replyText") or "What service do you need?")
        if updates.get("location"):
            label, suggestion = location_label(updates["location"])
            if suggestion:
                return await self.capture_booking_location(phone_number, label)
            updated = await session_manager.update_session(phone_number, {"location": label})
        if not updates and response.get("replyText"):
            if response.get("source") == "local" and has_draft(updated):
                if updated.get("step") == "PROVIDER_SELECTION" and updated.get("matchedProviders"):
                    return await whatsapp_service.send_message(phone_number, messages.provider_list(updated["matchedProviders"]))
                return await self.advance_booking(phone_number, updated)
            return await whatsapp_service.send_message(phone_number, response["replyText"])
        return await self.advance_booking(phone_number, updated)

    async def capture_booking_location(self, phone_number, text):
        label, suggestion = location_label(text)
        if not label or len(label) < 2 or label.isdigit():
            return await whatsapp_service.send_message(phone_number, "Please send your area and city, for example *Lugbe, Abuja*, or a location pin.")
        if suggestion:
            await session_manager.update_session(phone_number, {"step": "CONFIRM_LOCATION", "pendingLocation": suggestion})
            return await whatsapp_service.send_interactive_buttons(phone_number, f"Did you mean *{suggestion}*?", [
                {"id": "LOCATION_YES", "title": "Yes, that's correct"},
                {"id": "LOCATION_NO", "title": "Change location"},
            ])
        return await self.save_booking_location(phone_number, label)

    async def save_booking_location(self, phone_number, label):
        updated = await session_manager.update_session(phone_number, {
            "location": label, "pendingLocation": None, "matchedProviders": [], "wasenderChoices": [],
        })
        return await self.advance_booking(phone_number, updated)

    async def resume_booking(self, phone_number, session):
        if session.get("step") == "CONFIRM_LOCATION" and session.get("pendingLocation"):
            return await whatsapp_service.send_interactive_buttons(phone_number, f"Welcome back. Did you mean *{session['pendingLocation']}* for *{session['service']}*?", [
                {"id": "LOCATION_YES", "title": "Yes, that's correct"},
                {"id": "LOCATION_NO", "title": "Change location"},
            ])
        if session.get("step") == "PROVIDER_SELECTION" and session.get("matchedProviders"):
            return await whatsapp_service.send_message(phone_number, messages.provider_list(session["matchedProviders"]))
        return await self.advance_booking(phone_number, session)

    async def edit_booking(self, phone_number, session, action):
        if action == "EDIT_SERVICE":
            await session_manager.update_session(phone_number, {"step": "CATEGORY_SELECTION", "editingService": True})
            menu = messages.customer_category_selection()
            return await whatsapp_service.send_interactive_list(phone_number, menu["text"], menu["buttonLabel"], menu["sections"])
        field = {"EDIT_LOCATION": "location", "EDIT_DATE": "date", "EDIT_TIME": "time"}[action]
        updated = await session_manager.update_session(phone_number, {
            field: None, "matchedProviders": [], "isConfirmed": False, "wasenderChoices": [],
        })
        return await self.advance_booking(phone_number, updated)

    async def send_main_menu(self, phone_number: str, user: Dict[str, Any]) -> Dict[str, Any]:
        current = await session_manager.get_session(phone_number)
        if has_draft(current):
            await session_manager.update_session(phone_number, {"step": "MAIN_MENU"})
        first_name = get_user_name(user).split(" ")[0] or "Friend"
        if user.get("role") == "provider":
            pending = await self.repository.get_provider_pending_bookings(get_user_id(user))
            menu = messages.provider_main_menu(first_name, len(pending))
            return await whatsapp_service.send_interactive_buttons(
                phone_number, menu["text"], menu["buttons"]
            )

        menu = messages.customer_main_menu(first_name, reusable_request(get_memory(user)))
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
        user = await self.repository.update_user_location(
            get_user_id(user),
            label=location_label,
            coordinates={"latitude": lat, "longitude": lng},
        )

        session = await session_manager.get_session(phone_number)
        step = session.get("step")

        # 1. Customer onboarding step
        if step == "CUSTOMER_SETUP_LOCATION":
            await self.repository.mark_onboarded(user)
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
            return await self.save_booking_location(phone_number, location_label)

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

    async def handle_service_search(self, phone_number: str, message: str, session: Dict[str, Any]) -> Dict[str, Any]:
        return await self.handle_natural_message(phone_number, message, session)

    async def hydrate_usage_memory(self, user):
        memory = get_memory(user)
        if not memory["enabled"] or memory.get("lastRequest") or user.get("role") != "customer":
            return memory
        try:
            for booking in await self.repository.get_customer_bookings(user["id"], limit=5):
                if booking.get("status") in {"cancelled", "failed"}:
                    continue
                service = booking.get("service") or {}
                address = booking.get("address") or {}
                if not isinstance(service, dict) or not isinstance(address, dict):
                    continue
                details = {"service": service.get("category") or service.get("name"),
                           "location": address.get("whatsapp_location"), "time": booking.get("start_time")}
                if details["service"] and details["location"]:
                    return await self.repository.update_usage_memory(user, details=details, booking=booking)
        except Exception:
            logger.warning("Could not load older booking preferences")
        return memory

    async def remember_usage(self, phone_number, session, user=None, booking=None):
        try:
            user = user or await self.repository.find_or_create_whatsapp_user(phone_number)
            if not get_memory(user)["enabled"]:
                return
            memory = await self.repository.update_usage_memory(user, details=session, booking=booking)
            await session_manager.update_session(phone_number, {"usageMemory": prompt_memory(memory)})
        except Exception:
            # A preference write must not prevent a booking or provider search.
            logger.warning("Could not save usage memory; booking flow continues")

    async def handle_confirmation_response(
        self,
        phone_number: str,
        message: str,
        session: Dict[str, Any],
        action_id: Optional[str] = None,
        user: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = message.lower().strip()
        if text in CONFIRMATIONS or action_id == "CONFIRM_YES":
            if not ai_service.normalize_date(session.get("date")) or not ai_service.normalize_time(session.get("time")):
                await session_manager.update_session(phone_number, {"step": "AWAITING_DATETIME"})
                return await whatsapp_service.send_message(phone_number, messages.ask_date_time(session))

            problem = schedule_problem(session)
            if problem:
                await session_manager.update_session(phone_number, {"step": "AWAITING_DATETIME"})
                return await whatsapp_service.send_message(phone_number, problem)

            return await self.find_and_present_providers(phone_number, session, user)

        if text in {"no", "change details", "edit"}:
            menu = messages.edit_booking_menu()
            await session_manager.update_session(phone_number, {"step": "EDIT_BOOKING"})
            return await whatsapp_service.send_interactive_list(phone_number, menu["text"], menu["buttonLabel"], menu["sections"])
        updates = schedule_updates(message, session)
        if updates:
            updated = await session_manager.update_session(phone_number, {**updates, "matchedProviders": []})
            return await self.advance_booking(phone_number, updated)
        return await whatsapp_service.send_message(phone_number, "Your booking details are saved. Reply *yes* to find providers, *change details* to edit, or *cancel* to start over.")

    async def handle_date_time_capture(
        self, phone_number: str, message: str, session: Dict[str, Any]
    ) -> Dict[str, Any]:
        updates = schedule_updates(message, session)
        updated = await session_manager.update_session(phone_number, updates)
        return await self.advance_booking(phone_number, updated)

    async def find_and_present_providers(
        self, phone_number: str, session: Dict[str, Any], user=None
    ) -> Dict[str, Any]:
        await self.remember_usage(phone_number, session, user=user)
        providers = await self.repository.find_matching_services(
            service=session.get("service"),
            location=session.get("location"),
            limit=3,
        )

        if not providers:
            await session_manager.update_session(
                phone_number, {"step": "AWAITING_LOCATION", "isConfirmed": False, "matchedProviders": [], "wasenderChoices": []}
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
            selection = message.strip().lower()
            for word, number in {"first": "1", "second": "2", "third": "3"}.items():
                if re.search(r"\b" + word + r"\b", selection) and not is_question(selection):
                    selection = number
                    break
            numbered = re.search(r"\b(?:number|option|provider)\s*([1-3])\b", selection)
            if numbered and not is_question(selection):
                selection = numbered[1]
            choice_index = int(selection) - 1
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

            await self.remember_usage(phone_number, session, user=user, booking=booking)

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
