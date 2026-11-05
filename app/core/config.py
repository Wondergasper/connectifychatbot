import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Server
    PORT: int = 8000
    ENVIRONMENT: str = "development"

    # Supabase (PostgreSQL)
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")

    # Meta WhatsApp Cloud API
    WHATSAPP_PROVIDER: str = "meta"
    WASENDER_API_KEY: Optional[str] = None
    WASENDER_WEBHOOK_SECRET: Optional[str] = None
    WHATSAPP_TOKEN: Optional[str] = os.getenv("WHATSAPP_TOKEN")
    WHATSAPP_PHONE_ID: Optional[str] = os.getenv("WHATSAPP_PHONE_ID")
    WHATSAPP_APP_SECRET: Optional[str] = os.getenv("WHATSAPP_APP_SECRET")
    VERIFY_TOKEN: str = os.getenv("VERIFY_TOKEN", "connectify_verify_token")

    # Google Gemini AI
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")

    # Paystack Payments
    PAYSTACK_SECRET_KEY: Optional[str] = os.getenv("PAYSTACK_SECRET_KEY")

    # Marketplace Business Rules
    PLATFORM_COMMISSION_RATE: float = 0.10  # 10% take-rate

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in ("production", "prod")


settings = Settings()
