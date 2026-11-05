import logging
from typing import Dict, Any, List, Optional
import httpx
from app.core.config import settings

logger = logging.getLogger("connectify.whatsapp_service")

MAX_SEND_ATTEMPTS = 2
GRAPH_API_VERSION = "v18.0"


class WhatsAppService:
    def __init__(self):
        self.token = settings.WHATSAPP_TOKEN
        self.phone_id = settings.WHATSAPP_PHONE_ID
        self._listeners = []

    def add_listener(self, callback):
        self._listeners.append(callback)

    def remove_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify_listeners(self, to: str, message_data: Dict[str, Any]):
        for listener in list(self._listeners):
            try:
                listener(to, message_data)
            except Exception as e:
                logger.error(f"[WhatsAppService] Listener error: {e}")

    async def _send_text_menu(self, to: str, text: str, choices: List[Dict[str, str]]) -> Dict[str, Any]:
        from app.whatsapp.session import session_manager

        await session_manager.update_session(to, {"wasenderChoices": choices})
        lines = [text, ""] + [f"{i}. {item.get('title', '')}" for i, item in enumerate(choices, 1)]
        lines.append("Reply with the number of your choice.")
        return await self.send_message(to, "\n".join(lines))

    async def send_message(self, to: str, text: str, attempt: int = 1) -> Dict[str, Any]:
        """Sends a plain text message to a user via Meta WhatsApp Cloud API."""
        self._notify_listeners(to, {"type": "text", "to": to, "text": text})

        if settings.WHATSAPP_PROVIDER.lower() == "wasender":
            if not settings.WASENDER_API_KEY:
                logger.error("WasenderAPI key is not configured")
                return {"error": "WasenderAPI key is not configured"}
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        "https://www.wasenderapi.com/api/send-message",
                        json={"to": to, "text": text},
                        headers={"Authorization": f"Bearer {settings.WASENDER_API_KEY}"},
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.error("WasenderAPI send failed: %s", exc)
                return {"error": "WasenderAPI send failed"}

        if not self.token or not self.phone_id:
            logger.warning(f"[WhatsAppService] (Simulated Text) -> {to}:\n{text}")
            return {"simulated": True, "to": to, "text": text}

        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self.phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code in [200, 201]:
                logger.info(f"[WhatsAppService] Message sent to {to}: {response.status_code}")
                return response.json()

            logger.error(
                f"[WhatsAppService] Error sending to {to} (HTTP {response.status_code}): {response.text}"
            )
            if attempt < MAX_SEND_ATTEMPTS and (response.status_code >= 500 or response.status_code == 429):
                return await self.send_message(to, text, attempt + 1)
            return {"error": response.text, "status_code": response.status_code}
        except Exception as e:
            logger.error(f"[WhatsAppService] Network exception sending to {to}: {e}")
            if attempt < MAX_SEND_ATTEMPTS:
                return await self.send_message(to, text, attempt + 1)
            return {"error": str(e)}

    async def send_interactive_buttons(
        self,
        to: str,
        text: str,
        buttons: List[Dict[str, str]],
        options: Optional[Dict[str, str]] = None,
        attempt: int = 1,
    ) -> Dict[str, Any]:
        """
        Sends an interactive button message (up to 3 buttons) via Meta WhatsApp Cloud API.
        """
        options = options or {}
        if settings.WHATSAPP_PROVIDER.lower() == "wasender":
            return await self._send_text_menu(to, text, buttons[:3])
        formatted_buttons = [
            {
                "type": "reply",
                "reply": {
                    "id": str(btn.get("id", ""))[:256],
                    "title": str(btn.get("title", ""))[:20],
                },
            }
            for btn in buttons[:3]
        ]

        self._notify_listeners(to, {
            "type": "buttons",
            "to": to,
            "text": text,
            "buttons": formatted_buttons,
            "header": options.get("header"),
            "footer": options.get("footer"),
        })

        if not self.token or not self.phone_id:
            button_labels = "  ".join(
                f"[{b['reply']['title']} ({b['reply']['id']})]" for b in formatted_buttons
            )
            logger.warning(
                f"[WhatsAppService] (Simulated Buttons) -> {to}:\n{text}\n{button_labels}"
            )
            return {
                "simulated": True,
                "to": to,
                "text": text,
                "buttons": formatted_buttons,
            }

        interactive_payload: Dict[str, Any] = {
            "type": "button",
            "body": {"text": text},
            "action": {"buttons": formatted_buttons},
        }

        if options.get("header"):
            interactive_payload["header"] = {
                "type": "text",
                "text": str(options["header"])[:60],
            }
        if options.get("footer"):
            interactive_payload["footer"] = {
                "type": "text",
                "text": str(options["footer"])[:60],
            }

        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self.phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive_payload,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code in [200, 201]:
                logger.info(f"[WhatsAppService] Interactive buttons sent to {to}: {response.status_code}")
                return response.json()

            logger.error(
                f"[WhatsAppService] Error sending buttons to {to} (HTTP {response.status_code}): {response.text}"
            )
            if attempt < MAX_SEND_ATTEMPTS and (response.status_code >= 500 or response.status_code == 429):
                return await self.send_interactive_buttons(to, text, buttons, options, attempt + 1)

            # Fallback to text message with numbered options
            fallback_lines = [text, ""]
            for idx, b in enumerate(buttons):
                fallback_lines.append(f"{idx + 1}. {b.get('title')}")
            return await self.send_message(to, "\n".join(fallback_lines))
        except Exception as e:
            logger.error(f"[WhatsAppService] Exception sending buttons to {to}: {e}")
            fallback_lines = [text, ""]
            for idx, b in enumerate(buttons):
                fallback_lines.append(f"{idx + 1}. {b.get('title')}")
            return await self.send_message(to, "\n".join(fallback_lines))

    async def send_interactive_list(
        self,
        to: str,
        text: str,
        button_label: str,
        sections: List[Dict[str, Any]],
        options: Optional[Dict[str, str]] = None,
        attempt: int = 1,
    ) -> Dict[str, Any]:
        """
        Sends an interactive list message (up to 10 rows across sections) via Meta WhatsApp Cloud API.
        """
        options = options or {}
        if settings.WHATSAPP_PROVIDER.lower() == "wasender":
            rows = [row for section in sections for row in section.get("rows", [])][:10]
            return await self._send_text_menu(to, text, rows)
        formatted_sections = []
        for sec in sections:
            formatted_rows = []
            for row in (sec.get("rows") or [])[:10]:
                item = {
                    "id": str(row.get("id", ""))[:200],
                    "title": str(row.get("title", ""))[:24],
                }
                if row.get("description"):
                    item["description"] = str(row["description"])[:72]
                formatted_rows.append(item)

            formatted_sections.append({
                "title": str(sec.get("title") or "Options")[:24],
                "rows": formatted_rows,
            })

        self._notify_listeners(to, {
            "type": "list",
            "to": to,
            "text": text,
            "buttonLabel": button_label,
            "sections": formatted_sections,
            "header": options.get("header"),
            "footer": options.get("footer"),
        })

        if not self.token or not self.phone_id:
            logger.warning(f"[WhatsAppService] (Simulated List) -> {to} [{button_label}]:\n{text}")
            for s in formatted_sections:
                for r in s["rows"]:
                    logger.warning(f"  • [{r['id']}] {r['title']}: {r.get('description', '')}")
            return {
                "simulated": True,
                "to": to,
                "text": text,
                "buttonLabel": button_label,
                "sections": formatted_sections,
            }

        interactive_payload: Dict[str, Any] = {
            "type": "list",
            "body": {"text": text},
            "action": {
                "button": str(button_label or "Select")[:20],
                "sections": formatted_sections,
            },
        }

        if options.get("header"):
            interactive_payload["header"] = {
                "type": "text",
                "text": str(options["header"])[:60],
            }
        if options.get("footer"):
            interactive_payload["footer"] = {
                "type": "text",
                "text": str(options["footer"])[:60],
            }

        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self.phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": interactive_payload,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code in [200, 201]:
                logger.info(f"[WhatsAppService] Interactive list sent to {to}: {response.status_code}")
                return response.json()

            logger.error(
                f"[WhatsAppService] Error sending list to {to} (HTTP {response.status_code}): {response.text}"
            )
            if attempt < MAX_SEND_ATTEMPTS and (response.status_code >= 500 or response.status_code == 429):
                return await self.send_interactive_list(to, text, button_label, sections, options, attempt + 1)

            # Fallback to text
            fallback_lines = [text]
            for sec in sections:
                fallback_lines.append(f"\n*{sec.get('title')}*")
                for i, r in enumerate(sec.get("rows") or []):
                    desc = f" - {r['description']}" if r.get("description") else ""
                    fallback_lines.append(f"{i + 1}. {r.get('title')}{desc}")
            return await self.send_message(to, "\n".join(fallback_lines))
        except Exception as e:
            logger.error(f"[WhatsAppService] Exception sending list to {to}: {e}")
            fallback_lines = [text]
            for sec in sections:
                fallback_lines.append(f"\n*{sec.get('title')}*")
                for i, r in enumerate(sec.get("rows") or []):
                    desc = f" - {r['description']}" if r.get("description") else ""
                    fallback_lines.append(f"{i + 1}. {r.get('title')}{desc}")
            return await self.send_message(to, "\n".join(fallback_lines))

    async def send_template_message(
        self,
        to: str,
        template_name: str,
        components: Optional[List[Dict[str, Any]]] = None,
        language_code: str = "en",
        attempt: int = 1,
    ) -> Dict[str, Any]:
        """
        Sends a pre-approved Meta WhatsApp Message Template for re-engaging users
        outside the 24-hour customer service window.
        """
        if settings.WHATSAPP_PROVIDER.lower() == "wasender":
            return {"error": "Meta templates are not supported by the Wasender transport"}
        components = components or []
        self._notify_listeners(to, {
            "type": "template",
            "to": to,
            "templateName": template_name,
            "components": components,
            "languageCode": language_code,
        })
        if not self.token or not self.phone_id:
            logger.warning(
                f"[WhatsAppService] (Simulated Template: {template_name}) -> {to}: {components}"
            )
            return {
                "simulated": True,
                "to": to,
                "templateName": template_name,
                "components": components,
                "languageCode": language_code,
            }

        url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{self.phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": components,
            },
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code in [200, 201]:
                logger.info(f"[WhatsAppService] Template message sent to {to}: {response.status_code}")
                return response.json()

            logger.error(f"[WhatsAppService] Template send failed ({response.status_code}): {response.text}")
            return {"error": response.text, "status_code": response.status_code}
        except Exception as e:
            logger.error(f"[WhatsAppService] Exception sending template to {to}: {e}")
            return {"error": str(e)}


whatsapp_service = WhatsAppService()
