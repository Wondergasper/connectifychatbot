import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import get_supabase_client
from app.core.redis import get_redis_client, close_redis_client
from app.whatsapp.webhook import router as whatsapp_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("connectify.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Connectify WhatsApp Backend (FastAPI)...")
    get_supabase_client()
    await get_redis_client()
    yield
    # Shutdown
    logger.info("Shutting down Connectify WhatsApp Backend...")
    await close_redis_client()


app = FastAPI(
    title="Connectify WhatsApp Marketplace API",
    description="WhatsApp-first local service marketplace backend built with FastAPI, PostgreSQL/Supabase, Redis, and Gemini AI.",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health Check Endpoints
@app.get("/")
@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "Connectify WhatsApp Marketplace",
        "version": "2.0.0",
        "environment": settings.ENVIRONMENT,
    }


# Quiet third-party HTTP request logging to avoid leaking query parameters
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Mount WhatsApp Webhook routers
# 1. Primary path: /api/whatsapp/webhook
app.include_router(whatsapp_router, prefix="/api/whatsapp/webhook", tags=["WhatsApp"])
# 2. Direct path: /webhook (frequently configured in Meta developer dashboard)
app.include_router(whatsapp_router, prefix="/webhook", tags=["WhatsApp Webhook Direct"])

# Mount Paystack Webhook router
from app.payments.webhook import router as paystack_router
app.include_router(paystack_router, prefix="/api/paystack/webhook", tags=["Payments"])

# 3. Interactive WhatsApp Web Simulator (Browser UI) - Disabled in production
if not settings.is_production:
    from app.simulator.router import router as simulator_router
    app.include_router(simulator_router, tags=["Simulator"])
