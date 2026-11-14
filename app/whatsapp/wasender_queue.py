"""Durable Wasender inbox/outbox. SQLite coordinates FIFO consumers across workers.

Use a persistent local volume shared by all workers on ONE host. Separate hosts
must use a shared broker instead. Delivery is at least once across process crashes.
"""
import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx

from app.core.config import settings
from app.whatsapp.session import session_manager, session_phone

logger = logging.getLogger("connectify.wasender_queue")
LEASE_SECONDS = 60
LEASE_HEARTBEAT_SECONDS = 15


class WasenderQueue:
    def __init__(self, path=None):
        self.path = path
        self.tasks = []

    @contextmanager
    def _connect(self):
        path = Path(self.path or settings.WASENDER_QUEUE_DB)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                available REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT, updated REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(kind, state, seq);
            CREATE TABLE IF NOT EXISTS queue_clock (id INTEGER PRIMARY KEY, next_send REAL NOT NULL);
            INSERT OR IGNORE INTO queue_clock VALUES (1, 0);
            """)
            with db:
                yield db
        finally:
            db.close()

    def _insert(self, job_id, kind, payload):
        with self._connect() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO jobs(id, kind, payload, updated) VALUES (?, ?, ?, ?)",
                (job_id, kind, json.dumps(payload), time.time()),
            ).rowcount
        return bool(inserted)

    async def enqueue_incoming(self, message_id, phone, incoming):
        phone = session_phone(phone)
        job_id = "in:" + hashlib.sha256(f"{phone}:{message_id}".encode()).hexdigest()
        return await asyncio.to_thread(self._insert, job_id, "in", {"phone": phone, "incoming": incoming})

    async def enqueue_outgoing(self, phone, text, choices=None):
        phone = session_phone(phone)
        if not phone.isascii() or not phone.isdigit() or not 7 <= len(phone) <= 15:
            raise ValueError("Wasender recipient must be an international phone number")
        job_id = "out:" + uuid.uuid4().hex
        choices_step = (await session_manager.get_session(phone)).get("step") if choices is not None else None
        await asyncio.to_thread(self._insert, job_id, "out", {
            "phone": phone, "text": text, "choices": choices, "choicesStep": choices_step,
        })
        return {"queued": True, "id": job_id}

    def _claim(self, kind):
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Sending is serial per Wasender session. Incoming work can run for
            # different senders, but never overtake an earlier reply by that sender.
            if kind == "out" and db.execute(
                "SELECT 1 FROM jobs WHERE kind='out' AND state='processing' AND available>? LIMIT 1", (now,),
            ).fetchone():
                return None
            row = db.execute(
                """SELECT j.* FROM jobs j
                   WHERE j.kind=? AND j.state IN ('pending', 'processing') AND j.available<=?
                     AND NOT EXISTS (
                       SELECT 1 FROM jobs earlier
                       WHERE earlier.kind=j.kind AND earlier.seq<j.seq
                         AND earlier.state IN ('pending', 'processing')
                         AND json_extract(earlier.payload, '$.phone')=json_extract(j.payload, '$.phone')
                     )
                   ORDER BY j.seq LIMIT 1""",
                (kind, now),
            ).fetchone()
            if not row or row["available"] > now:
                return None
            if kind == "out" and db.execute("SELECT next_send FROM queue_clock WHERE id=1").fetchone()[0] > now:
                return None
            db.execute(
                "UPDATE jobs SET state='processing', available=?, attempts=attempts+1, updated=? WHERE id=?",
                (now + LEASE_SECONDS, now, row["id"]),
            )
            return {**dict(row), "attempts": row["attempts"] + 1, "payload": json.loads(row["payload"])}

    def _finish(self, job_id, state, delay=0, error=None):
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET state=?, available=?, error=?, updated=? WHERE id=?",
                (state, time.time() + delay, error, time.time(), job_id),
            )

    def _pace(self, delay):
        with self._connect() as db:
            db.execute("UPDATE queue_clock SET next_send=? WHERE id=1", (time.time() + delay,))

    def _renew_lease(self, job):
        with self._connect() as db:
            return db.execute(
                "UPDATE jobs SET available=? WHERE id=? AND state='processing' AND attempts=?",
                (time.time() + LEASE_SECONDS, job["id"], job["attempts"]),
            ).rowcount

    async def _heartbeat(self, job):
        while True:
            await asyncio.sleep(LEASE_HEARTBEAT_SECONDS)
            if not await asyncio.to_thread(self._renew_lease, job):
                return

    async def _incoming(self, payload):
        from app.whatsapp.conversation import conversation_manager
        from app.whatsapp.service import whatsapp_service

        phone, incoming = payload["phone"], payload["incoming"]
        if incoming["type"] == "text":
            session = await session_manager.get_session(phone)
            choices = session.get("wasenderChoices") or []
            if choices and session.get("wasenderChoicesStep") and session["wasenderChoicesStep"] != session.get("step"):
                choices = []
                await session_manager.update_session(phone, {"wasenderChoices": []})
            text = incoming["text"]
            # Old quick-reply menus must never intercept a date or location.
            if session.get("step") in {"AWAITING_LOCATION", "AWAITING_DATETIME"}:
                if choices:
                    await session_manager.update_session(phone, {"wasenderChoices": []})
                await conversation_manager.handle_incoming_message(phone, incoming)
                return
            navigation = {"cancel", "exit", "restart", "reset", "menu", "options", "home",
                          "hi", "hello", "hey", "help", "start", "wallet", "balance",
                          "bookings", "jobs", "requests", "find a service", "find service",
                          "services", "categories", "browse services", "trades", "resume", "continue", "main menu"}
            navigation.update({"memory", "my preferences", "what do you remember", "what do you remember about me",
                               "what do you remember about me?", "memory on", "memory off", "forget my preferences",
                               "same as last time", "same again", "use my last request", "book again"})
            if choices and text.lower() not in navigation:
                choice = next((item for item in choices if str(item.get("title", "")).lower() == text.lower()), None)
                if not choice and text.isascii() and text.isdigit() and len(text) <= 2 and 1 <= int(text) <= len(choices):
                    choice = choices[int(text) - 1]
                if not choice and text.lower() in {"yes", "correct", "confirm", "looks good", "go ahead", "yes please", "proceed"}:
                    choice = next((item for item in choices if item.get("id") in {"CONFIRM_YES", "LOCATION_YES"}), None)
                if not choice and text.lower() in {"no", "change details"}:
                    choice = next((item for item in choices if item.get("id") in {"CMD_EDIT_BOOKING", "LOCATION_NO"}), None)
                if choice:
                    incoming = {"type": "interactive", "text": choice.get("title", ""), "actionId": choice.get("id")}
                    await session_manager.update_session(phone, {"wasenderChoices": []})
                else:
                    if text.isascii() and text.isdigit():
                        await whatsapp_service.send_message(phone, f"Please choose a number from 1 to {len(choices)}, or tell me what you need.")
                        return
                    # Menus are optional shortcuts. Natural language must reach
                    # the assistant instead of being rejected by the transport.
            elif choices:
                await session_manager.update_session(phone, {"wasenderChoices": []})
        await conversation_manager.handle_incoming_message(phone, incoming)

    @staticmethod
    def retry_delay(response, data):
        values = [1.0]
        for value in (response.headers.get("retry-after"), response.headers.get("x-ratelimit-reset"),
                      response.headers.get("x-ratelimit-daily-reset"),
                      data.get("retry_after"), (data.get("data") or {}).get("retry_after") if isinstance(data.get("data"), dict) else None):
            try:
                number = float(value)
                if math.isfinite(number) and number > 0:
                    values.append(number)
            except (TypeError, ValueError):
                pass
        return max(values)

    async def _outgoing(self, job):
        payload = job["payload"]
        if not settings.WASENDER_API_KEY:
            await asyncio.to_thread(self._finish, job["id"], "pending", 60, "missing_api_key")
            logger.error("Wasender API key missing; outgoing message retained")
            return
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://www.wasenderapi.com/api/send-message",
                headers={"Authorization": f"Bearer {settings.WASENDER_API_KEY}"},
                json={"to": "+" + payload["phone"], "text": payload["text"]},
            )
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.status_code == 429:
            delay = max(self.retry_delay(response, data), settings.WASENDER_SEND_INTERVAL_SECONDS)
            await asyncio.to_thread(self._pace, delay)
            await asyncio.to_thread(self._finish, job["id"], "pending", delay, "rate_limited")
            logger.warning("Wasender rate limited job %s; retry in %.1f seconds", job["id"], delay)
            return
        # Configuration/account failures remain queued so reconnecting or fixing
        # credentials does not discard notifications. Never log response bodies.
        message = str(data.get("message", "")).lower()
        disconnected = any(text in message for text in (
            "session is not connected", "session not connected", "session is disconnected", "logged_out",
        ))
        if response.status_code in (401, 402, 403, 409, 503) or disconnected or "no active subscription" in message:
            await asyncio.to_thread(self._finish, job["id"], "pending", 60, f"http_{response.status_code}")
            logger.error("Wasender HTTP %s; check API key, session, and subscription; message retained", response.status_code)
            return
        if response.status_code >= 500:
            raise RuntimeError("provider_unavailable")
        if response.is_success and data.get("success") is True:
            if payload.get("choices") is not None:
                current = await session_manager.get_session(payload["phone"])
                if payload.get("choicesStep") is None or payload["choicesStep"] == current.get("step"):
                    await session_manager.update_session(payload["phone"], {
                        "wasenderChoices": payload["choices"], "wasenderChoicesStep": payload.get("choicesStep"),
                    })
            await asyncio.to_thread(self._finish, job["id"], "done")
            return
        await asyncio.to_thread(self._finish, job["id"], "failed", 0, f"rejected_http_{response.status_code}")
        logger.error("Wasender rejected outgoing job %s (HTTP %s); retained for inspection", job["id"], response.status_code)

    async def process_one(self, kind):
        job = await asyncio.to_thread(self._claim, kind)
        if not job:
            return False
        started = time.perf_counter()
        queue_wait_ms = max(0, round((time.time() - job["updated"]) * 1000))
        heartbeat = asyncio.create_task(self._heartbeat(job))
        try:
            if kind == "in":
                await self._incoming(job["payload"])
                await asyncio.to_thread(self._finish, job["id"], "done")
            else:
                await asyncio.to_thread(self._pace, settings.WASENDER_SEND_INTERVAL_SECONDS)
                await self._outgoing(job)
        except asyncio.CancelledError:
            await asyncio.to_thread(self._finish, job["id"], "pending")
            raise
        except Exception:
            delay = min(300, 2 ** min(job["attempts"], 8))
            state = "failed" if job["attempts"] >= 8 else "pending"
            await asyncio.to_thread(self._finish, job["id"], state, delay, "processing_failed")
            logger.error("Wasender %s job %s failed; state=%s", kind, job["id"], state)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            logger.info("Wasender %s job %s attempt=%s queue_wait_ms=%s processing_ms=%s",
                        kind, job["id"], job["attempts"], queue_wait_ms,
                        round((time.perf_counter() - started) * 1000))
        return True

    async def _worker(self, kind):
        while True:
            try:
                if not await self.process_one(kind):
                    await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Wasender queue storage unavailable; retrying")
                await asyncio.sleep(1)

    async def start(self):
        # Open before serving HTTP so a bad/unwritable queue path fails startup.
        await asyncio.to_thread(self._initialize)
        if not self.tasks:
            self.tasks = [asyncio.create_task(self._worker("in")) for _ in range(settings.WASENDER_INBOX_WORKERS)]
            self.tasks.append(asyncio.create_task(self._worker("out")))

    def _initialize(self):
        with self._connect() as db:
            # Migrate leases left by the previous 15-minute recovery policy.
            db.execute("UPDATE jobs SET available=MIN(available, ?) WHERE state='processing'",
                       (time.time() + LEASE_SECONDS,))
            # Keep deduplication IDs and completed payloads for a week.
            db.execute("DELETE FROM jobs WHERE state='done' AND updated < ?", (time.time() - 7 * 86400,))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []


wasender_queue = WasenderQueue()
