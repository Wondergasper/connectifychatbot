# WasenderAPI setup

1. Create a WhatsApp session in the WasenderAPI dashboard and link your phone using the QR code.
2. In `.env`, set `WHATSAPP_PROVIDER=wasender`, `WASENDER_API_KEY` to the session API key, and `WASENDER_WEBHOOK_SECRET` to the session's webhook secret. Keep both secrets private.
3. In the session's webhook settings, configure the publicly accessible HTTPS URL `https://YOUR_HOST/api/wasender/webhook` and enable `messages.received`. Configure the same webhook secret in the dashboard and the application.
4. Restart the application and send a message to the linked WhatsApp account. Interactive choices are delivered as numbered text; reply with the corresponding number.

Set `WHATSAPP_PROVIDER=meta` to use the existing Meta transport. Wasender sessions are managed in the Wasender dashboard; this application does not create or link sessions. Meta message templates are not supported by the Wasender transport.

References: [Getting started](https://www.wasenderapi.com/api-docs/getting-started/getting-started-with-wasenderapi), [webhook setup](https://app.wasenderapi.com/api-docs/webhooks/webhook-setup/), and [message received event](https://app.wasenderapi.com/api-docs/webhooks/webhook-message-received).
