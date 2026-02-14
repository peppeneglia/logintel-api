from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/v1/health")
async def health_check():
    settings = get_settings()
    return {
        "status": "healthy",
        "version": "0.1.0",
        "environment": settings.app_env,
    }
