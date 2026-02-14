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
    description="Predictive route intelligence for logistics",
    version="0.1.0",
    lifespan=lifespan,
)

register_error_handlers(app)

# Middleware order: TimingMiddleware wraps RequestIdMiddleware
# (added in reverse — last added runs first)
app.add_middleware(TimingMiddleware)
app.add_middleware(RequestIdMiddleware)

app.include_router(health_router)
app.include_router(predictions_router)
app.include_router(analytics_router)
