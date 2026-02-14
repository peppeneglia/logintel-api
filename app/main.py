from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.errors import register_error_handlers
from app.logging_config import setup_logging
from app.middleware import RequestIdMiddleware, TimingMiddleware
from app.routes.analytics import router as analytics_router
from app.routes.health import router as health_router
from app.routes.predictions import router as predictions_router
from app.services.cache import close_redis, init_redis
from app.services.http_client import close_client, init_client
from app.services.supabase import init_supabase
from app.stores import init_stores


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage startup/shutdown of shared resources."""
    settings = get_settings()
    setup_logging(settings.log_level)
    init_client()
    init_redis(settings.upstash_redis_url)
    init_supabase()
    init_stores()
    yield
    await close_redis()
    await close_client()


app = FastAPI(
    title="Logintel API",
    description=(
        "## Weather-Aware Delay Prediction for Road Freight\n\n"
        "Logintel API predicts weather-related delays for road freight transport "
        "along specific routes and suggests alternatives when delays exceed a threshold.\n\n"
        "### Key features\n"
        "- **Delay prediction** — per-segment breakdown with confidence score\n"
        "- **Alternative routes** — suggested when predicted delay > 20 min\n"
        "- **Feedback loop** — submit actual delays to improve future predictions\n"
        "- **Accuracy analytics** — MAE, within-10/20 min rates, breakdown by weather type\n\n"
        "### How it works\n"
        "1. Calculate route via OpenRouteService\n"
        "2. Sample weather forecast at points every 50 km along the route\n"
        "3. Apply smart heuristics (road type, altitude, time of day, calibration)\n"
        "4. Return total delay, per-segment details, and confidence score\n\n"
        "### Authentication\n"
        "All endpoints (except `/v1/health`) require authentication via:\n"
        "- **Bearer token** — `Authorization: Bearer <JWT>`\n"
        "- **API key** — `X-API-Key: <key>`\n"
    ),
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "predictions",
            "description": "Create, retrieve, and list delay predictions. Submit feedback on actual delays.",
        },
        {
            "name": "analytics",
            "description": "Accuracy metrics and calibration status computed from user feedback.",
        },
        {
            "name": "health",
            "description": "Service health check with dependency status, metrics, and active alerts.",
        },
    ],
    contact={
        "name": "Logintel Support",
        "email": "support@logintel.io",
        "url": "https://logintel.io",
    },
    license_info={
        "name": "Proprietary",
    },
    servers=[
        {"url": "https://api.logintel.io", "description": "Production"},
        {"url": "https://sandbox.logintel.io", "description": "Sandbox (coming soon)"},
    ],
)

register_error_handlers(app)

# Middleware order: TimingMiddleware wraps RequestIdMiddleware
# (added in reverse — last added runs first)
app.add_middleware(TimingMiddleware)
app.add_middleware(RequestIdMiddleware)

app.include_router(health_router)
app.include_router(predictions_router)
app.include_router(analytics_router)
