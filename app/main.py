from fastapi import FastAPI
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Logintel API",
    description="Predictive route intelligence for logistics",
    version="0.1.0",
)


@app.get("/v1/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "0.1.0",
        "environment": settings.app_env,
    }
