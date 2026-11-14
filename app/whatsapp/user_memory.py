"""Small, explicit usage preferences stored in app_users.profile.assistantMemory."""
from datetime import datetime, timezone


def label(value, limit=120):
    return str(value).strip()[:limit] if isinstance(value, str) and value.strip() else None


def clean_memory(value):
    source = value if isinstance(value, dict) else {}
    result = {"version": 1, "enabled": source.get("enabled") is not False}
    for key, limit in (("recentServices", 5), ("recentLocations", 3), ("bookingIds", 20)):
        values = source.get(key)
        result[key] = list(dict.fromkeys(item for item in map(label, values) if item))[:limit] if isinstance(values, list) else []
    counts = source.get("serviceCounts")
    result["serviceCounts"] = {str(key)[:80]: min(max(value, 0), 1000000) for key, value in list(counts.items())[:10]
                              if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)} if isinstance(counts, dict) else {}
    for key in ("lastRequest", "lastBooking"):
        record = source.get(key)
        if isinstance(record, dict):
            allowed = ("service", "location", "recordedAt") if key == "lastRequest" else ("service", "location", "provider", "date", "time", "recordedAt")
            result[key] = {field: clean for field in allowed if (clean := label(record.get(field)))}
    return result


def get_memory(user):
    return clean_memory((user.get("profile") or {}).get("assistantMemory"))


def record_usage(previous, details, booking=None):
    memory = clean_memory(previous)
    if not memory["enabled"]:
        return memory
    service, location = label(details.get("service")), label(details.get("location"))
    if not service or not location:
        return memory
    now = datetime.now(timezone.utc).isoformat()
    memory["lastRequest"] = {"service": service, "location": location, "recordedAt": now}
    memory["recentServices"] = list(dict.fromkeys([service] + memory["recentServices"]))[:5]
    memory["recentLocations"] = list(dict.fromkeys([location] + memory["recentLocations"]))[:3]
    if booking and (booking_id := label(booking.get("id"))):
        if booking_id not in memory["bookingIds"]:
            memory["serviceCounts"][service] = memory["serviceCounts"].get(service, 0) + 1
            memory["serviceCounts"] = dict(sorted(memory["serviceCounts"].items(), key=lambda item: item[1], reverse=True)[:10])
            memory["bookingIds"] = [booking_id] + memory["bookingIds"][:19]
        memory["lastBooking"] = {"service": service, "location": location, "recordedAt": now}
        for field, value in (("date", booking.get("date")), ("time", booking.get("start_time") or details.get("time")),
                             ("provider", (booking.get("provider") or {}).get("name"))):
            if clean := label(value):
                memory["lastBooking"][field] = clean
    return memory


def prompt_memory(memory):
    memory = clean_memory(memory)
    if not memory["enabled"]:
        return {}
    return {key: memory[key] for key in ("recentServices", "recentLocations", "serviceCounts", "lastRequest", "lastBooking") if memory.get(key)}


def reusable_request(memory):
    memory = clean_memory(memory)
    return (memory.get("lastRequest") or memory.get("lastBooking")) if memory["enabled"] else None


def describe_memory(user):
    memory = get_memory(user)
    if not memory["enabled"]:
        return "Personalized memory is off. Say *memory on* to let me remember your service and location preferences again."
    lines = ["Here's the usage information I've saved for you:"]
    if memory["recentServices"]:
        lines.append("• Recent services: " + ", ".join(memory["recentServices"]))
    if memory["recentLocations"]:
        lines.append("• Recent areas: " + "; ".join(memory["recentLocations"]))
    if memory["serviceCounts"]:
        service, count = max(memory["serviceCounts"].items(), key=lambda item: item[1])
        if count > 1:
            lines.append(f"• Most requested service: {service} ({count} booking requests)")
    if booking := memory.get("lastBooking"):
        lines.append(f"• Last booking request: {booking.get('service')} in {booking.get('location')}" + (f" on {booking['date']}" if booking.get("date") else ""))
    if len(lines) == 1:
        lines.append("No service preferences yet. I'll remember them when you confirm a service request.")
    lines.append("Say *forget my preferences* to clear this, or *memory off* to stop personalization.")
    return "\n".join(lines)
