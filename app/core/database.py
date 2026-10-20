import logging
from typing import Optional, Any
from app.core.config import settings

logger = logging.getLogger("connectify.database")

_supabase_client: Optional[Any] = None


def get_supabase_client():
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        logger.warning("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not configured. Running in simulated mode.")
        return None

    try:
        from supabase import create_client, ClientOptions
        _supabase_client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY,
            options=ClientOptions(
                postgrest_client_timeout=15,
                storage_client_timeout=15
            )
        )
        logger.info("Connected to Supabase PostgreSQL.")
        return _supabase_client
    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        return None


def set_supabase_client(client: Any):
    """Allows test fixtures to inject mock Supabase clients."""
    global _supabase_client
    _supabase_client = client
