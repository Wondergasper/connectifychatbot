"""
WhatsApp Message Templates for Connectify Nigeria
Formatted for Meta WhatsApp Cloud API (plain text, interactive buttons, interactive lists).
"""
from typing import Dict, Any, List, Optional


def welcome() -> str:
    return (
        "Welcome to *Connectify Nigeria*! 🇳🇬\n\n"
        "Your on-demand marketplace for trusted local service providers "
        "(plumbers, electricians, tailors, cleaners, mechanics, etc.).\n\n"
        "First, what is your full name?"
    )


def role_selection(name: str = "") -> Dict[str, Any]:
    return {
        "text": f"Nice to meet you, *{name}*! 👋\n\nHow would you like to use Connectify today?",
        "buttons": [
            {"id": "ROLE_CUSTOMER", "title": "I need a service"},
            {"id": "ROLE_PROVIDER", "title": "I am a provider"},
        ],
    }


def customer_main_menu(first_name: str = "") -> Dict[str, Any]:
    return {
        "text": (
            f"Hello {first_name}! How can Connectify help you today?\n\n"
            "💡 _Tip: You can also type what you need directly, e.g._\n"
            '_"I need a generator mechanic in Surulere tomorrow 10am"_'
        ),
        "buttons": [
            {"id": "CMD_FIND_SERVICE", "title": "🔍 Find a Service"},
            {"id": "CMD_MY_BOOKINGS", "title": "📅 My Bookings"},
            {"id": "CMD_WALLET", "title": "💳 My Wallet"},
        ],
    }


def provider_main_menu(first_name: str = "", pending_count: int = 0) -> Dict[str, Any]:
    alert_text = (
        f"🔔 You have *{pending_count}* pending job request(s) waiting!"
        if pending_count > 0
        else "You are currently active and ready for bookings."
    )
    return {
        "text": f"Welcome back, *{first_name}*! 🛠️\n\n{alert_text}",
        "buttons": [
            {"id": "CMD_PENDING_JOBS", "title": f"📋 Requests ({pending_count})"},
            {"id": "CMD_ACTIVE_JOBS", "title": "⚡ Active Jobs"},
            {"id": "CMD_WALLET", "title": "💰 Earnings & Bal"},
        ],
    }


def help_message(first_name: str = "", role: str = "customer") -> str:
    name_str = f" {first_name}" if first_name else ""
    if role == "provider":
        return (
            f"Hello{name_str}!\n\n"
            "As a registered Connectify Provider, you can:\n"
            "• Reply *requests* to see new job bookings.\n"
            "• Reply *active* to see confirmed bookings.\n"
            "• Reply *wallet* to check your balance and earnings.\n"
            "• Reply *menu* anytime to view options."
        )
    return (
        f"Hi{name_str}! I am Connectify.\n\n"
        "Tell me what you need in one sentence, for example:\n"
        '"I need a plumber in Ikeja tomorrow 2pm"\n\n'
        "Or type *menu* to see your options, or *cancel* to restart."
    )


def no_providers(service: Optional[str] = None, location: Optional[str] = None) -> str:
    svc = service or "service"
    loc = location or "that area"
    return (
        f"Sorry, I could not find available {svc} providers near {loc} right now.\n\n"
        'Try a nearby area (e.g. "Ikeja", "Yaba", "Lekki"), or type *cancel* to start over.'
    )


def provider_list(providers: List[Dict[str, Any]]) -> str:
    count = len(providers)
    lines = [f"I found {count} verified provider{'s' if count != 1 else ''} for you:\n"]

    for idx, provider in enumerate(providers):
        lines.append(
            f"*{idx + 1}. {provider.get('displayName')}* "
            f"(⭐ {provider.get('rating')} · {provider.get('completedJobs')} jobs)"
        )
        if provider.get("category"):
            lines.append(f"   Service: {provider.get('category')}")
        if provider.get("priceLabel"):
            lines.append(f"   Est. Price: {provider.get('priceLabel')}")
        if provider.get("locationLabel"):
            lines.append(f"   Location: {provider.get('locationLabel')}")
        lines.append("")

    lines.append("Reply with the number (*1*, *2*, etc.) to select your provider.")
    return "\n".join(lines)


def confirm_summary(session: Dict[str, Any]) -> str:
    lines = [
        "📝 *Please confirm your booking request:*\n",
        f"*Service:* {session.get('service')}",
        f"*Location:* {session.get('location')}",
        f"*Date:* {session.get('date') or 'As soon as possible'}",
    ]
    if session.get("time"):
        lines.append(f"*Time:* {session.get('time')}")
    if session.get("budget"):
        lines.append(f"*Budget:* ₦{session.get('budget')}")
    if session.get("urgency") == "emergency":
        lines.append("🚨 *Urgent Request*")
    lines.append("\nIs this correct? Reply *YES* to find providers or *NO* to restart.")
    return "\n".join(lines)


def ask_date_time() -> str:
    return (
        "Which date and time works best for you?\n\n"
        "Examples:\n"
        '• "today 5pm"\n'
        '• "tomorrow 10:30am"\n'
        '• "2026-10-20 09:00"'
    )


def booking_created(provider: Dict[str, Any], booking: Dict[str, Any]) -> str:
    return (
        "✅ *Booking Request Sent!*\n\n"
        f"Your request with *{provider.get('displayName')}* has been sent.\n"
        "We have notified the provider via WhatsApp.\n\n"
        "You will receive an instant notification here as soon as they confirm!"
    )


def provider_new_booking_alert(booking: Dict[str, Any]) -> Dict[str, Any]:
    customer = booking.get("customer") or {}
    amount = float(booking.get("total_amount") or 0)
    text = (
        "🔔 *NEW BOOKING REQUEST!*\n\n"
        f"*Customer:* {customer.get('name') or 'Customer'}\n"
        f"*Phone:* {customer.get('phone') or 'Private'}\n"
        f"*Date:* {booking.get('date')}\n"
        f"*Time:* {booking.get('start_time') or booking.get('time')}\n"
        f"*Amount:* ₦{amount:,.0f}\n"
        f"*Notes:* {booking.get('notes') or 'None'}\n\n"
        "Please accept or decline this job:"
    )
    booking_id = booking.get("id")
    return {
        "text": text,
        "buttons": [
            {"id": f"ACCEPT_BOOKING_{booking_id}", "title": "✅ Accept Job"},
            {"id": f"DECLINE_BOOKING_{booking_id}", "title": "❌ Decline"},
        ],
    }


def customer_booking_confirmed(booking: Dict[str, Any], provider: Dict[str, Any]) -> str:
    amount = float(booking.get("total_amount") or 0)
    return (
        "🎉 *Your Booking is Confirmed!*\n\n"
        f"*Provider:* {provider.get('name') or 'Your Provider'}\n"
        f"*Date & Time:* {booking.get('date')} at {booking.get('start_time') or booking.get('time')}\n"
        f"*Amount:* ₦{amount:,.0f}\n\n"
        f"You can reach your provider directly at: {provider.get('phone') or ''}\n"
        "Type *menu* anytime to manage your bookings."
    )


def customer_booking_declined(booking: Dict[str, Any], provider: Dict[str, Any]) -> str:
    return (
        "ℹ️ *Booking Update*\n\n"
        f"The provider *{provider.get('name') or 'Provider'}* is unavailable for the selected slot.\n"
        "Would you like me to find another nearby provider for you? Reply with your service request to search again."
    )


def booking_failed(session: Dict[str, Any]) -> str:
    return (
        "I couldn't create the booking request because that slot is currently occupied.\n\n"
        'Reply with a different date/time (e.g. "tomorrow 4pm") or type *cancel* to start over.'
    )


def wallet_summary(wallet: Dict[str, Any], role: str = "customer") -> str:
    balance_str = wallet.get("formatted") or f"₦{float(wallet.get('balance') or 0):,.0f}"
    instruction = (
        "To withdraw your earnings to your bank account, please reply *withdraw*."
        if role == "provider"
        else "To top up your wallet balance, Paystack top-up links can be generated upon checkout."
    )
    return (
        "💳 *Connectify Digital Wallet*\n\n"
        f"*Current Balance:* {balance_str}\n\n"
        f"{instruction}"
    )


def booking_list(bookings: List[Dict[str, Any]], role: str = "customer") -> str:
    if not bookings:
        return "📭 You currently have no bookings on record."

    header = "📋 *Your Recent Jobs:*\n" if role == "provider" else "📅 *Your Recent Bookings:*\n"
    lines = [header]

    for idx, b in enumerate(bookings):
        other_party = (b.get("customer") or {}).get("name") if role == "provider" else (b.get("provider") or {}).get("name")
        status = b.get("status") or "pending"
        status_emoji = "✅" if status == "confirmed" else "⏳" if status == "pending" else "🏁" if status == "completed" else "❌"
        svc_name = (b.get("service") or {}).get("name") if isinstance(b.get("service"), dict) else "Service"
        amount = float(b.get("total_amount") or 0)

        lines.append(f"{idx + 1}. {status_emoji} *{svc_name}* with {other_party or 'User'}")
        lines.append(f"   Date: {b.get('date')} {b.get('start_time') or ''}")
        lines.append(f"   Status: *{status.upper()}* | ₦{amount:,.0f}\n")

    return "\n".join(lines)


def provider_category_selection() -> Dict[str, Any]:
    return {
        "text": (
            "🛠️ *Select your primary trade/service:*\n\n"
            "Choose the service category you offer to customers in your area:"
        ),
        "buttonLabel": "Select Trade",
        "sections": [
            {
                "title": "Popular Trades",
                "rows": [
                    {"id": "TRADE_PLUMBING", "title": "Plumbing", "description": "Pipes, leaks, fittings & boreholes"},
                    {"id": "TRADE_ELECTRICAL", "title": "Electrical", "description": "Wiring, fixtures & panel repair"},
                    {"id": "TRADE_AC", "title": "AC & Refrigeration", "description": "AC install, gas refill & repair"},
                    {"id": "TRADE_GENERATOR", "title": "Generator Repair", "description": "Diesel & petrol gen servicing"},
                    {"id": "TRADE_CLEANING", "title": "Home & Office Clean", "description": "Deep cleaning & fumigation"},
                    {"id": "TRADE_CARPENTRY", "title": "Carpentry", "description": "Furniture making & wood repairs"},
                    {"id": "TRADE_PAINTING", "title": "Painting", "description": "Interior, exterior & POP design"},
                ],
            }
        ],
    }


def customer_category_selection() -> Dict[str, Any]:
    return {
        "text": (
            "🔍 *Find a Verified Service Provider*\n\n"
            "Please select the service category you need from our verified trades below:"
        ),
        "buttonLabel": "Browse Categories",
        "sections": [
            {
                "title": "Verified Trades & Services",
                "rows": [
                    {"id": "CATEGORY_PLUMBING", "title": "Plumbing", "description": "Pipes, leaks, fittings & boreholes"},
                    {"id": "CATEGORY_ELECTRICAL", "title": "Electrical", "description": "Wiring, fixtures & panel repair"},
                    {"id": "CATEGORY_AC", "title": "AC & Refrigeration", "description": "AC install, gas refill & repair"},
                    {"id": "CATEGORY_GENERATOR", "title": "Generator Repair", "description": "Diesel & petrol gen servicing"},
                    {"id": "CATEGORY_CLEANING", "title": "Home & Office Clean", "description": "Deep cleaning & fumigation"},
                    {"id": "CATEGORY_CARPENTRY", "title": "Carpentry", "description": "Furniture making & wood repairs"},
                    {"id": "CATEGORY_PAINTING", "title": "Painting", "description": "Interior, exterior & POP design"},
                ],
            }
        ],
    }


def customer_location_prompt(name: str) -> str:
    first_name = name.split(" ")[0] if name else "Friend"
    return (
        f"Nice to meet you, *{first_name}*! 👋\n\n"
        "📍 Which neighborhood or area in Lagos do you stay?\n"
        "• Reply with your area (e.g. *Ikeja*, *Surulere*, *Lekki*, *Yaba*)\n"
        "• Or tap the 📍 button on WhatsApp to send your location pin."
    )


def customer_onboarding_completed(first_name: str, location: str) -> Dict[str, Any]:
    return {
        "text": (
            f"🎉 You are all set, *{first_name}*!\n"
            f"📍 Primary location set to: *{location}*\n\n"
            "How can Connectify help you today?\n"
            "💡 _Tip: You can search anytime, e.g. 'I need a plumber tomorrow 10am'_"
        ),
        "buttons": [
            {"id": "CMD_FIND_SERVICE", "title": "🔍 Find a Service"},
            {"id": "CMD_MY_BOOKINGS", "title": "📅 My Bookings"},
            {"id": "CMD_WALLET", "title": "💳 My Wallet"},
        ],
    }


def provider_location_prompt(category: str) -> str:
    return (
        f"🛠️ Excellent! You selected *{category}*.\n\n"
        "📍 What area(s) do you operate from or cover in Lagos?\n"
        "• Reply with your area (e.g. *Surulere & Yaba*, *Ikeja*, *Lekki*)\n"
        "• Or send your current workshop / shop location pin 📍"
    )


def location_prompt_for_category(category: str) -> str:
    return (
        f"Great choice! You selected *{category}*.\n\n"
        "📍 Where in Lagos do you need this service?\n"
        "• Reply with your area (e.g. *Ikeja*, *Surulere*, *Lekki*, *Yaba*)\n"
        "• Or tap the 📍 button to share your current location pin."
    )


def provider_price_prompt(category: str) -> str:
    return (
        f"Great! You have selected *{category}*.\n\n"
        "What is your standard hourly rate or inspection fee in Naira? (e.g. *5000*)"
    )


def provider_service_created(category: str, price: float) -> str:
    return (
        "🎉 *Congratulations! Your profile is LIVE!* 🚀\n\n"
        f"• *Service:* {category}\n"
        f"• *Base/Inspection Rate:* ₦{float(price):,.0f}\n\n"
        "You are now visible to customers in your area.\n"
        "You will receive an instant WhatsApp alert whenever a new customer requests your service!"
    )


def dispute_alert(ticket_id: str) -> str:
    return (
        "🚨 *Connectify Safety & Dispute Notice*\n\n"
        f"We take your safety and satisfaction seriously. Ticket *#{ticket_id}* has been opened.\n"
        "• *Action Taken:* Payout release for this booking has been paused.\n"
        "• *Next Step:* Our resolution specialist will contact you on this chat within 30 minutes."
    )


def escrow_payout_notice(payout_amount: float, commission: float) -> str:
    return (
        "💰 *Escrow Payout Credited!*\n\n"
        f"*Net Payout:* ₦{float(payout_amount):,.0f}\n"
        f"*Platform Fee (10%):* ₦{float(commission):,.0f}\n\n"
        "The funds have been added to your Connectify wallet balance and are available for instant withdrawal."
    )


def customer_job_completion_prompt(booking: Dict[str, Any], provider: Dict[str, Any]) -> Dict[str, Any]:
    prov_name = provider.get("name") if provider else "Provider"
    booking_id = booking.get("id")
    return {
        "text": (
            f"🏁 *Job Completed by {prov_name}!*\n\n"
            "Please confirm that the work was done to your satisfaction to release payment from escrow:"
        ),
        "buttons": [
            {"id": f"RATE_5_{booking_id}", "title": "⭐ Great (5 Stars)"},
            {"id": f"RATE_4_{booking_id}", "title": "👍 Good (4 Stars)"},
            {"id": f"DISPUTE_{booking_id}", "title": "⚠️ Report Issue"},
        ],
    }
