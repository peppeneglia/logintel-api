from fastapi import FastAPI

from app.errors import register_error_handlers
from app.routes.health import router as health_router
from app.routes.predictions import router as predictions_router

app = FastAPI(
    title="Logintel API",
    description="Predictive route intelligence for logistics",
    version="0.1.0",
)

register_error_handlers(app)

app.include_router(health_router)
app.include_router(predictions_router)
