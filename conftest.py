"""Keep automated tests from using a configured live WhatsApp transport."""
import os

os.environ["WHATSAPP_PROVIDER"] = "meta"
os.environ["WASENDER_API_KEY"] = ""
os.environ["WASENDER_WEBHOOK_SECRET"] = ""
