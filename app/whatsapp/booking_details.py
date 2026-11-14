"""Deterministic booking details; these never depend on an AI response."""
import re
from datetime import datetime

from app.whatsapp import ai_service

BOOKING_STEPS = {
    "AWAITING_LOCATION", "CONFIRM_LOCATION", "AWAITING_DATETIME",
    "AWAITING_CONFIRMATION", "PROVIDER_SELECTION", "EDIT_BOOKING",
}


def location_label(text):
    label = re.sub(r"\s+", " ", str(text or "").strip()).title()
    if re.search(r"\blubge\b", label, re.I) and re.search(r"\babuja\b", label, re.I):
        return label, "Lugbe, Abuja"
    if re.fullmatch(r"lugbe\s*,?\s+abuja", label, re.I):
        label = "Lugbe, Abuja"
    return label, None


def schedule_updates(message, session):
    """A day-only reply must not overwrite an already captured 10am time."""
    date = ai_service.normalize_date(
        message, allow_day_only=bool(session.get("time") and not session.get("date")),
    )
    time = ai_service.normalize_time(
        message, allow_bare_hour=bool(session.get("date") and not session.get("time") and not date),
    )
    updates = {}
    if date:
        updates["date"] = date
    if time:
        updates["time"] = time
    return updates


def schedule_problem(session):
    date = ai_service.normalize_date(session.get("date"))
    time = ai_service.normalize_time(session.get("time"))
    if not date:
        return None
    now = ai_service.nigeria_now()
    if date < now.date().isoformat():
        return "That date has passed. Which future date would you like? For example, 18 November."
    if time:
        scheduled = datetime.fromisoformat(f"{date}T{time}").replace(tzinfo=now.tzinfo)
        if scheduled <= now:
            return f"{time} on {date} has passed. Please choose a later time or another date."
    return None


def has_draft(session):
    return bool(session.get("service") and session.get("step") != "CATEGORY_SELECTION")
