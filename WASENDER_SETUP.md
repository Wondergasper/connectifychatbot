# WasenderAPI setup

1. Create a WhatsApp session in the WasenderAPI dashboard and link your phone using the QR code.
2. In `.env`, set `WHATSAPP_PROVIDER=wasender`, `WASENDER_API_KEY` to the session API key, and `WASENDER_WEBHOOK_SECRET` to the session's webhook secret. Keep both secrets private.
3. In the session's webhook settings, configure the publicly accessible HTTPS URL `https://YOUR_HOST/api/wasender/webhook` and enable `messages.received`. Configure the same webhook secret in the dashboard and the application.
4. Restart the application and send a message to the linked WhatsApp account. Interactive choices are delivered as numbered text; reply with the corresponding number.

Set `WHATSAPP_PROVIDER=meta` to use the existing Meta transport. Wasender sessions are managed in the Wasender dashboard; this application does not create or link sessions. Meta message templates are not supported by the Wasender transport.

## Delivery and workers

Incoming webhooks are validated and committed to a SQLite inbox before HTTP 200 is returned. Message IDs are deduplicated for seven days (completed records are cleaned on startup). Four inbox consumers process independent senders concurrently (`WASENDER_INBOX_WORKERS`, default 4), while replies from each sender stay in order. A slow or retrying sender does not block other conversations. Invalid menu replies preserve the current choices; type `menu`, `cancel`, or another navigation command to leave them.

Outgoing messages are committed to an outbox and sent in order. A queued response means accepted locally, not delivered to WhatsApp. Menu choices become active only after Wasender accepts the send. HTTP 429 responses respect `retry_after`, `Retry-After`, and rate-limit reset headers. Transient network/server errors retry with backoff; exhausted attempts and provider rejections remain in the database as `failed`. Missing credentials, disconnected-session errors, and HTTP 401/402/403/409/503 leave messages pending for automatic retries every minute. Check logs for job IDs and status codes; secrets and provider response bodies are not logged.

Set `WASENDER_SEND_INTERVAL_SECONDS=5` for a paid session with account protection, or `60` for trial accounts. The queue applies this spacing across recipients, not just per phone. Daily caps can still pause delivery. Do not disable account protection to work around a queue backlog.

The default queue database is `data/wasender.sqlite3`, configurable with `WASENDER_QUEUE_DB`. Keep this path on a persistent local disk and protect access because it contains message content. Run this deployment on one host, preferably with one Uvicorn worker:

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Workers on the same host must share the same SQLite file and Redis sessions. Separate hosts/replicas require a shared message broker instead of separate SQLite files. Use Redis for production conversation state; memory-only sessions do not survive a restart. Workers renew a one-minute lease every 15 seconds; after a crash, the job becomes eligible within one minute of the last renewal. Processing/delivery is at least once across crashes: a crash after an external side effect but before marking the job done can repeat that effect. Booking/payment operations must retain their own idempotency guarantees.

## Check your credentials without sending a message

From the project directory, run:

```powershell
.venv/Scripts/python.exe -m scripts.check_wasender
```

This reads the configured key, calls `GET /api/status`, and prints only configuration presence, queue counts, HTTP status, and a recognized connection status. It does not send messages or print credentials.

- HTTP 200 with `connected`: the key is accepted and WhatsApp is connected.
- HTTP 200 with `logged_out`, `expired`, or `disconnected`: reconnect the session using its QR/Passkey flow in the Wasender dashboard. Replacing a valid key alone will not reconnect WhatsApp.
- HTTP 401/403: compare `WASENDER_API_KEY` with the **session API key** in the dashboard. After changing `.env`, restart the app.
- Incoming webhook HTTP 403: compare the dashboard webhook secret with `WASENDER_WEBHOOK_SECRET`; this is separate from the API key.
- No incoming messages: confirm the public HTTPS URL ends in `/api/wasender/webhook` and `messages.received` is enabled. The status check cannot verify webhook settings.

After reconnecting, send a message yourself to the linked account from another number, finish onboarding, and test a provider booking alert with a numbered reply. This verifies both webhook delivery and outgoing messages.

## Slow replies

- `REDIS_URL` must point to a reachable Redis instance. `localhost` only works when Redis runs on the same host/container. Set it to an empty value to explicitly use memory sessions for development; use a real Redis URL in production. Failed connections now pause reconnects for 30 seconds instead of retrying during every session operation, with a bounded first connection attempt.
- Logs include `queue_wait_ms` and `processing_ms` per job. A large incoming processing time points to database/session/AI work; outgoing `rate limited` logs show the exact retry delay. No message text or phone is included in these timing logs.
- Wasender trial accounts permit only one outgoing message per minute. Paid account protection permits one every five seconds. Keep the interval appropriate to the account.
- Render Free services spin down after 15 minutes without inbound traffic and take about a minute to wake. They also lose local SQLite files on restart/spin-down. Use an always-on instance with a persistent disk for this deployment's queue. See [Render Free service limitations](https://render.com/docs/free) and [Wasender rate limits](https://www.wasenderapi.com/api-docs/rate-limits/understanding-rate-limits).

## Booking conversations

Simple dates and menu choices are handled immediately. Free-form requests, corrections, and follow-up questions reach Gemini throughout the booking journey, including while a numbered menu is displayed. `GEMINI_MODEL` defaults to `gemini-3.1-flash-lite`. There is no permanent five-call cutoff; temporary API failures/rate limits use a local parser for 30 seconds before trying Gemini again. Logs identify `source=gemini` or `source=local` and the fallback reason without exposing the API key.

Dates such as `18 November`, `November 18`, `18th`, `the 18th`, and `next Friday` are accepted. A bare `18` is treated as a day when the date is missing and a time is already saved, so it cannot overwrite `10am` with `18:00`. Yearless dates use the next occurrence; the full date is shown for confirmation. Dates use Nigeria time and past bookings are rejected. `Friday afternoon, same place` preserves the service/location, changes the date, and asks for an exact time instead of inventing an hour.

The bot asks only for missing details, confirms the complete request before searching, and keeps the rest of the draft when the user selects `Change details`. Location prompts accept area/city across Nigeria; actual provider coverage comes from the service search. `Lubge Abuja` requires confirmation before using the suggested `Lugbe, Abuja`. A failed provider search keeps the service and schedule while asking for another location.

Use `hi`, `resume`, or `continue` to resume an unfinished booking, including after visiting the main menu. Redis sessions retain drafts for 24 hours. A temporary Redis outage uses a local copy and synchronizes it back on recovery. A restart during an outage still requires working persistent Redis to restore the draft. Stale numbered menus cannot intercept free-form date/location replies. Completed onboarding is saved so returning users are not asked for their name again.

The latest 12 user/assistant messages (up to 800 characters each) are kept with the session to interpret references and follow-ups. Concurrent senders have separate reply captures. Reset clears this history. Provider records sent to Gemini include only display name, quoted price, rating, and area, excluding phone numbers and raw database payloads. AI can propose validated booking fields and answer questions; explicit backend actions still control provider selection, booking creation, and payments.

## Long-term usage memory in Supabase

The existing `app_users.profile.assistantMemory` JSON field stores each user's recent confirmed services (up to five), areas (up to three), latest confirmed request, latest booking request, and service booking counts. Counts are deduplicated against the latest 20 booking IDs; this is a personalization summary rather than a financial/audit ledger. Unconfirmed draft edits do not become saved preferences. The first returning greeting can initialize memory from an existing non-cancelled booking with a recorded WhatsApp location. Current name, role, and onboarding details continue to live in the user profile.

This uses the existing profile column and needs no SQL migration. Profile writes compare the previous JSON value and retry concurrent edits so other profile settings are preserved. A failed preference write is logged and does not block provider search or booking creation.

Memory survives Redis expiry, process restarts, and redeployments because it is read from Supabase for the authenticated WhatsApp sender. Returning customers can choose `Use last request` or say `same as last time` to reuse the service and area. Every reused request asks for a fresh date and time and still requires confirmation. The assistant does not assume an earlier booking was fulfilled or paid.

Commands:
- `memory` or `what do you remember about me`: view saved usage preferences.
- `memory off`: stop using and updating personalized service preferences.
- `memory on`: enable future usage memory again.
- `forget my preferences`: clear the preference summary and disable personalization. This does not delete account identity, booking records, or the current booking draft.

Raw conversation transcripts are not stored permanently in Supabase by this feature. Short-term chat history remains in the 24-hour session. Gemini sees only the sanitized preference summary, excluding internal booking IDs. A normal `cancel` resets the conversation draft/history; it does not erase long-term preferences.

References: [Getting started](https://www.wasenderapi.com/api-docs/getting-started/getting-started-with-wasenderapi), [webhook setup](https://app.wasenderapi.com/api-docs/webhooks/webhook-setup/), and [message received event](https://app.wasenderapi.com/api-docs/webhooks/webhook-message-received).
