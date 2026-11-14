"""Bounded conversation memory, isolated between concurrent WhatsApp senders."""
from contextvars import ContextVar

reply_capture = ContextVar("connectify_reply_capture", default=None)
MAX_HISTORY = 12


def capture_reply(phone, message):
    capture = reply_capture.get()
    if capture is None or str(phone).lstrip("+") != capture["phone"]:
        return
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        capture["replies"].append({"role": "assistant", "text": text[:800]})


def history_tail(items):
    return [{"role": item["role"], "text": str(item.get("text", ""))[:800]}
            for item in items if isinstance(item, dict) and item.get("role") in {"user", "assistant"}][-MAX_HISTORY:]
