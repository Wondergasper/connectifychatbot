# WasenderAPI setup

1. Create a WhatsApp session in the WasenderAPI dashboard and link your phone using the QR code.
2. In `.env`, set `WHATSAPP_PROVIDER=wasender`, `WASENDER_API_KEY` to the session API key, and `WASENDER_WEBHOOK_SECRET` to the session's webhook secret. Keep both secrets private.
3. In the session's webhook settings, configure the publicly accessible HTTPS URL `https://YOUR_HOST/api/wasender/webhook` and enable `messages.received`. Configure the same webhook secret in the dashboard and the application.
4. Restart the application and send a message to the linked WhatsApp account. Interactive choices are delivered as numbered text; reply with the corresponding number.

Set `WHATSAPP_PROVIDER=meta` to use the existing Meta transport. Wasender sessions are managed in the Wasender dashboard; this application does not create or link sessions. Meta message templates are not supported by the Wasender transport.

## Delivery and workers

Incoming webhooks are validated and committed to a SQLite inbox before HTTP 200 is returned. Message IDs are deduplicated for seven days (completed records are cleaned on startup). A background consumer processes messages in order, so concurrent replies cannot advance a menu twice. Invalid menu replies preserve the current choices; type `menu`, `cancel`, or another navigation command to leave them.

Outgoing messages are committed to an outbox and sent in order. A queued response means accepted locally, not delivered to WhatsApp. Menu choices become active only after Wasender accepts the send. HTTP 429 responses respect `retry_after`, `Retry-After`, and rate-limit reset headers. Transient network/server errors retry with backoff; exhausted attempts and provider rejections remain in the database as `failed`. Missing credentials, disconnected-session errors, and HTTP 401/402/403/409/503 leave messages pending for automatic retries every minute. Check logs for job IDs and status codes; secrets and provider response bodies are not logged.

Set `WASENDER_SEND_INTERVAL_SECONDS=5` for a paid session with account protection, or `60` for trial accounts. The queue applies this spacing across recipients, not just per phone. Daily caps can still pause delivery. Do not disable account protection to work around a queue backlog.

The default queue database is `data/wasender.sqlite3`, configurable with `WASENDER_QUEUE_DB`. Keep this path on a persistent local disk and protect access because it contains message content. Run this deployment on one host, preferably with one Uvicorn worker:

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Workers on the same host must share the same SQLite file and Redis sessions. Separate hosts/replicas require a shared message broker instead of separate SQLite files. Use Redis for production conversation state; memory-only sessions do not survive a restart. A crashed worker's leased job is retried after 15 minutes. Processing/delivery is at least once across crashes: a crash after an external side effect but before marking the job done can repeat that effect. Booking/payment operations must retain their own idempotency guarantees.

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

References: [Getting started](https://www.wasenderapi.com/api-docs/getting-started/getting-started-with-wasenderapi), [webhook setup](https://app.wasenderapi.com/api-docs/webhooks/webhook-setup/), and [message received event](https://app.wasenderapi.com/api-docs/webhooks/webhook-message-received).
