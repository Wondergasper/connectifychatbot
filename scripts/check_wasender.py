"""Read-only key/session check. Run: python -m scripts.check_wasender"""
import asyncio
import sqlite3
from pathlib import Path

import httpx

from app.core.config import settings


async def main():
    print(f"WhatsApp provider: {settings.WHATSAPP_PROVIDER}")
    print(f"Wasender API key configured: {bool(settings.WASENDER_API_KEY)}")
    print(f"Webhook secret configured: {bool(settings.WASENDER_WEBHOOK_SECRET)}")
    path = Path(settings.WASENDER_QUEUE_DB).resolve()
    if path.exists():
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            for kind, state, count in db.execute("SELECT kind, state, count(*) FROM jobs GROUP BY kind, state"):
                print(f"Queue {kind}/{state}: {count}")
        finally:
            db.close()
    if not settings.WASENDER_API_KEY:
        print("Set WASENDER_API_KEY to this session's API key.")
        return 1
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get("https://www.wasenderapi.com/api/status", headers={
                "Authorization": f"Bearer {settings.WASENDER_API_KEY}",
            })
        print(f"Wasender status endpoint HTTP: {response.status_code}")
        if response.status_code in (401, 403):
            print("Authentication rejected: check the session API key in the dashboard.")
            return 1
        response.raise_for_status()
        status = response.json().get("status", "unknown")
        # Only print recognized status values, never an arbitrary API response.
        known = {"connected", "connecting", "disconnected", "need_scan", "need_passkey", "logged_out", "expired"}
        print(f"Session status: {status if status in known else 'unknown'}")
        if status != "connected":
            print("Reconnect/finish linking the WhatsApp session in the Wasender dashboard.")
            return 1
        print("API key accepted and session connected. Webhook URL/secret still need an incoming-message check.")
        return 0
    except (httpx.HTTPError, ValueError, AttributeError):
        print("Status check failed. Check network access and dashboard; no messages were sent.")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
