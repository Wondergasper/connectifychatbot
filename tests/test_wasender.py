import httpx
import pytest
from unittest.mock import AsyncMock

from app.core.config import settings
from app.main import app
from app.whatsapp.session import session_manager
from app.whatsapp.service import whatsapp_service
from app.whatsapp.conversation import conversation_manager
from app.whatsapp.wasender_queue import WasenderQueue, wasender_queue


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(wasender_queue, "path", tmp_path / "wasender.sqlite3")
    monkeypatch.setattr(settings, "WASENDER_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(settings, "WASENDER_API_KEY", "test-key")


def webhook_payload(text="1", message_id="message-1"):
    return {"event": "messages.received", "data": {"messages": {
        "key": {"id": message_id, "remoteJid": "2348001234567@s.whatsapp.net", "fromMe": False},
        "messageBody": text,
    }}}


async def post_webhook(payload):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/wasender/webhook", json=payload, headers={"X-Webhook-Signature": "secret"})


def mock_provider(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def queue_rows():
    with wasender_queue._connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY seq")]


def make_ready():
    with wasender_queue._connect() as db:
        db.execute("UPDATE jobs SET available=0 WHERE state='pending'")
        db.execute("UPDATE queue_clock SET next_send=0")


@pytest.mark.asyncio
async def test_wasender_webhook_authentication_and_menu_selection(monkeypatch):
    monkeypatch.setattr(settings, "WASENDER_WEBHOOK_SECRET", "secret")
    handler = AsyncMock()
    monkeypatch.setattr(conversation_manager, "handle_incoming_message", handler)
    await session_manager.update_session("+2348001234567", {"wasenderChoices": [{"id": "ROLE_CUSTOMER", "title": "Customer"}]})
    payload = webhook_payload()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/wasender/webhook", json=payload)).status_code == 403
        assert (await client.post("/api/wasender/webhook", json=payload, headers={"X-Webhook-Signature": "secret"})).status_code == 200
    handler.assert_not_awaited()  # HTTP acknowledgement does not wait for conversation work.
    assert await wasender_queue.process_one("in")
    handler.assert_awaited_once_with("2348001234567", {"type": "interactive", "text": "Customer", "actionId": "ROLE_CUSTOMER"})
    assert (await session_manager.get_session("2348001234567"))["wasenderChoices"] == []


@pytest.mark.asyncio
async def test_wasender_menu_sends_numbered_text(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "wasender")
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(200, json={"success": True, "data": {"status": "in_progress"}})
    mock_provider(monkeypatch, provider)
    result = await whatsapp_service.send_interactive_buttons("+2348001234567", "Choose", [{"id": "ROLE_CUSTOMER", "title": "Customer"}])
    assert result["queued"] is True
    assert not (await session_manager.get_session("2348001234567")).get("wasenderChoices")
    assert await wasender_queue.process_one("out")
    import json
    assert json.loads(requests[0].content) == {"to": "+2348001234567", "text": "Choose\n\n1. Customer\nReply with the number of your choice."}
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert (await session_manager.get_session("2348001234567"))["wasenderChoices"][0]["id"] == "ROLE_CUSTOMER"


@pytest.mark.asyncio
async def test_duplicate_reply_cannot_select_next_menu(monkeypatch):
    async def next_menu(phone, incoming):
        await session_manager.update_session(phone, {"wasenderChoices": [{"id": "NEXT", "title": "Next"}]})
    handler = AsyncMock(side_effect=next_menu)
    monkeypatch.setattr(conversation_manager, "handle_incoming_message", handler)
    await session_manager.update_session("2348001234567", {"wasenderChoices": [{"id": "FIRST", "title": "First"}]})
    assert (await post_webhook(webhook_payload())).json()["status"] == "received"
    assert await wasender_queue.process_one("in")
    # A new queue instance/process sees the same deduplication record.
    restarted = WasenderQueue(wasender_queue.path)
    assert not await restarted.enqueue_incoming("message-1", "+2348001234567", {"type": "text", "text": "1"})
    assert (await post_webhook(webhook_payload())).json()["status"] == "duplicate"
    assert not await wasender_queue.process_one("in")
    assert handler.await_count == 1
    assert (await session_manager.get_session("2348001234567"))["wasenderChoices"][0]["id"] == "NEXT"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["0", "99", "oops", "9999999999999999999999999"])
async def test_invalid_reply_preserves_menu(monkeypatch, invalid):
    handler = AsyncMock()
    monkeypatch.setattr(conversation_manager, "handle_incoming_message", handler)
    choices = [{"id": "ACCEPT_BOOKING_123", "title": "Accept Job"}]
    await session_manager.update_session("+2348001234567", {"wasenderChoices": choices})
    await post_webhook(webhook_payload(invalid))
    await wasender_queue.process_one("in")
    handler.assert_not_awaited()
    assert (await session_manager.get_session("2348001234567"))["wasenderChoices"] == choices
    await post_webhook(webhook_payload("1", "message-2"))
    await wasender_queue.process_one("in")
    assert handler.await_args.args[1]["actionId"] == "ACCEPT_BOOKING_123"


@pytest.mark.asyncio
async def test_menu_command_can_leave_numbered_choices(monkeypatch):
    handler = AsyncMock()
    monkeypatch.setattr(conversation_manager, "handle_incoming_message", handler)
    await session_manager.update_session("2348001234567", {"wasenderChoices": [{"id": "OLD"}]})
    await post_webhook(webhook_payload("menu"))
    await wasender_queue.process_one("in")
    handler.assert_awaited_once_with("2348001234567", {"type": "text", "text": "menu"})
    assert not (await session_manager.get_session("2348001234567"))["wasenderChoices"]


@pytest.mark.asyncio
async def test_rate_limit_retains_order_and_respects_delay(monkeypatch):
    calls = []
    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"retry_after": 60}, headers={"Retry-After": "45"})
        return httpx.Response(200, json={"success": True})
    mock_provider(monkeypatch, provider)
    await wasender_queue.enqueue_outgoing("2348001234567", "first")
    await wasender_queue.enqueue_outgoing("2348001234567", "second")
    import time
    before = time.time()
    await wasender_queue.process_one("out")
    rows = queue_rows()
    assert rows[0]["state"] == "pending"
    assert rows[0]["available"] >= before + 60
    assert rows[1]["attempts"] == 0
    assert not await wasender_queue.process_one("out")
    make_ready()
    await wasender_queue.process_one("out")
    assert queue_rows()[0]["state"] == "done"
    assert not await wasender_queue.process_one("out")  # pacing applies after success too


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 409, 503])
async def test_account_failures_retain_message(monkeypatch, status):
    mock_provider(monkeypatch, lambda request: httpx.Response(status, json={"message": "private provider text"}))
    await wasender_queue.enqueue_outgoing("2348001234567", "notification")
    await wasender_queue.process_one("out")
    assert queue_rows()[0]["state"] == "pending"
    assert queue_rows()[0]["error"] == f"http_{status}"


@pytest.mark.asyncio
async def test_network_error_is_retained_without_activating_menu(monkeypatch, caplog):
    def provider(request):
        raise httpx.ConnectError("private details", request=request)
    mock_provider(monkeypatch, provider)
    await wasender_queue.enqueue_outgoing("2348001234567", "menu", choices=[{"id": "X"}])
    await wasender_queue.process_one("out")
    assert queue_rows()[0]["state"] == "pending"
    assert not (await session_manager.get_session("2348001234567")).get("wasenderChoices")
    assert "test-key" not in caplog.text
    assert "private details" not in caplog.text


@pytest.mark.asyncio
async def test_provider_rejection_is_not_treated_as_success(monkeypatch):
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={"success": False}))
    await wasender_queue.enqueue_outgoing("2348001234567", "menu", choices=[{"id": "X"}])
    await wasender_queue.process_one("out")
    assert queue_rows()[0]["state"] == "failed"
    assert not (await session_manager.get_session("2348001234567")).get("wasenderChoices")


@pytest.mark.asyncio
async def test_fifo_claim_serializes_separate_workers():
    import asyncio
    await wasender_queue.enqueue_incoming("first", "2348001234567", {"type": "text", "text": "one"})
    await wasender_queue.enqueue_incoming("second", "2348001234567", {"type": "text", "text": "two"})
    other = WasenderQueue(wasender_queue.path)
    claims = await asyncio.gather(asyncio.to_thread(wasender_queue._claim, "in"), asyncio.to_thread(other._claim, "in"))
    assert sum(job is not None for job in claims) == 1
    claimed = next(job for job in claims if job)
    wasender_queue._finish(claimed["id"], "done")
    assert other._claim("in")["payload"]["incoming"]["text"] == "two"


@pytest.mark.asyncio
async def test_restart_recovers_expired_lease():
    await wasender_queue.enqueue_incoming("first", "2348001234567", {"type": "text", "text": "hi"})
    claimed = wasender_queue._claim("in")
    with wasender_queue._connect() as db:
        db.execute("UPDATE jobs SET available=0")
    assert WasenderQueue(wasender_queue.path)._claim("in")["id"] == claimed["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], {"event": "messages.received", "data": []}, webhook_payload(message_id="")])
async def test_malformed_payload_rejected(payload):
    assert (await post_webhook(payload)).status_code == 400


@pytest.mark.asyncio
async def test_lid_sender_and_location_are_normalized(monkeypatch):
    handler = AsyncMock()
    monkeypatch.setattr(conversation_manager, "handle_incoming_message", handler)
    payload = webhook_payload()
    message = payload["data"]["messages"]
    message["key"].update(remoteJid="123456@lid", cleanedSenderPn="2348001234567")
    message["message"] = {"locationMessage": {"degreesLatitude": 6.5, "degreesLongitude": 3.3}}
    assert (await post_webhook(payload)).status_code == 200
    await wasender_queue.process_one("in")
    assert handler.await_args.args[0] == "2348001234567"
    assert handler.await_args.args[1]["location"]["latitude"] == 6.5


@pytest.mark.asyncio
async def test_unavailable_storage_is_not_acknowledged(monkeypatch):
    monkeypatch.setattr(wasender_queue, "enqueue_incoming", AsyncMock(side_effect=OSError("disk unavailable")))
    assert (await post_webhook(webhook_payload())).status_code == 503


@pytest.mark.asyncio
async def test_disconnected_session_error_retains_message(monkeypatch):
    mock_provider(monkeypatch, lambda request: httpx.Response(422, json={"success": False, "message": "Session is not connected"}))
    await wasender_queue.enqueue_outgoing("2348001234567", "notification")
    await wasender_queue.process_one("out")
    assert queue_rows()[0]["state"] == "pending"


@pytest.mark.asyncio
async def test_queue_workers_start_and_stop_without_live_network(monkeypatch):
    import asyncio
    handled = asyncio.Event()
    async def process(payload):
        handled.set()
    monkeypatch.setattr(wasender_queue, "_incoming", process)
    await wasender_queue.enqueue_incoming("lifecycle", "2348001234567", {"type": "text", "text": "hello"})
    try:
        await wasender_queue.start()
        await asyncio.wait_for(handled.wait(), timeout=5)
        assert len(wasender_queue.tasks) == settings.WASENDER_INBOX_WORKERS + 1
    finally:
        await wasender_queue.stop()
    assert not wasender_queue.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("jid,from_me", [("2348001234567@g.us", False), ("2348001234567@s.whatsapp.net", True), ("12345@lid", False)])
async def test_ignored_senders_are_not_enqueued(jid, from_me):
    payload = webhook_payload()
    payload["data"]["messages"]["key"].update(remoteJid=jid, fromMe=from_me)
    assert (await post_webhook(payload)).json()["status"] == "ignored"
    assert queue_rows() == []


@pytest.mark.asyncio
async def test_session_reset_clears_both_phone_spellings():
    await session_manager.update_session("+2348001234567", {"step": "PROVIDER_SELECTION"})
    assert (await session_manager.get_session("2348001234567"))["step"] == "PROVIDER_SELECTION"
    await session_manager.clear_session("2348001234567")
    assert (await session_manager.get_session("+2348001234567"))["step"] == "init"


@pytest.mark.asyncio
async def test_retrying_sender_does_not_delay_other_senders():
    await wasender_queue.enqueue_incoming("slow", "2348001234567", {"type": "text", "text": "hi"})
    first = wasender_queue._claim("in")
    wasender_queue._finish(first["id"], "pending", 60)
    await wasender_queue.enqueue_incoming("same", "2348001234567", {"type": "text", "text": "name"})
    await wasender_queue.enqueue_incoming("other", "2348007654321", {"type": "text", "text": "hi"})
    assert wasender_queue._claim("in")["payload"]["phone"] == "2348007654321"
    assert wasender_queue._claim("in") is None


@pytest.mark.asyncio
async def test_slow_sender_does_not_occupy_all_inbox_workers(monkeypatch):
    import asyncio
    slow_started, release_slow, fast_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def process(payload):
        if payload["phone"] == "2348001234567":
            slow_started.set()
            await release_slow.wait()
        else:
            fast_done.set()
    monkeypatch.setattr(wasender_queue, "_incoming", process)
    await wasender_queue.enqueue_incoming("slow", "2348001234567", {"type": "text", "text": "hi"})
    await wasender_queue.enqueue_incoming("fast", "2348007654321", {"type": "text", "text": "hi"})
    try:
        await wasender_queue.start()
        await asyncio.wait_for(slow_started.wait(), 5)
        await asyncio.wait_for(fast_done.wait(), 5)
        assert not release_slow.is_set()
    finally:
        release_slow.set()
        await wasender_queue.stop()


@pytest.mark.asyncio
async def test_outgoing_stays_serial_across_recipients():
    await wasender_queue.enqueue_outgoing("2348001234567", "first")
    await wasender_queue.enqueue_outgoing("2348007654321", "second")
    assert wasender_queue._claim("out") is not None
    assert wasender_queue._claim("out") is None


@pytest.mark.asyncio
async def test_active_jobs_renew_the_short_lease(monkeypatch):
    import asyncio
    import time
    import app.whatsapp.wasender_queue as queue_module
    renewed, release = asyncio.Event(), asyncio.Event()
    original = wasender_queue._renew_lease
    loop = asyncio.get_running_loop()
    def renew(job):
        result = original(job)
        loop.call_soon_threadsafe(renewed.set)
        return result
    async def process(payload):
        await release.wait()
    monkeypatch.setattr(queue_module, "LEASE_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(wasender_queue, "_renew_lease", renew)
    monkeypatch.setattr(wasender_queue, "_incoming", process)
    await wasender_queue.enqueue_incoming("lease", "2348001234567", {"type": "text", "text": "hi"})
    task = asyncio.create_task(wasender_queue.process_one("in"))
    try:
        await asyncio.wait_for(renewed.wait(), 5)
        assert queue_rows()[0]["available"] > time.time() + 50
        assert wasender_queue._claim("in") is None
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_old_attempt_cannot_renew_reclaimed_job():
    await wasender_queue.enqueue_incoming("lease", "2348001234567", {"type": "text", "text": "hi"})
    old = wasender_queue._claim("in")
    with wasender_queue._connect() as db:
        db.execute("UPDATE jobs SET available=0")
    new = wasender_queue._claim("in")
    assert wasender_queue._renew_lease(old) == 0
    assert wasender_queue._renew_lease(new) == 1


@pytest.mark.asyncio
async def test_startup_shortens_legacy_fifteen_minute_leases():
    import time
    await wasender_queue.enqueue_incoming("legacy", "2348001234567", {"type": "text", "text": "hi"})
    wasender_queue._claim("in")
    with wasender_queue._connect() as db:
        db.execute("UPDATE jobs SET available=?", (time.time() + 900,))
    wasender_queue._initialize()
    assert queue_rows()[0]["available"] <= time.time() + 60
