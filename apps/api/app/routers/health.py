from fastapi import APIRouter

from app.core.config import settings
from app.core.database import check_db_health

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def get_health():
    db_alive = await check_db_health()
    return {
        "status": "ok",
        "stage": "Stage 1 - Platform Skeleton",
        "environment": settings.ENVIRONMENT,
        "use_fake_integrations": settings.USE_FAKE_INTEGRATIONS,
        "database_connected": db_alive,
        "models": {
            "cheap": settings.CHEAP_MODEL,
            "strong": settings.STRONG_MODEL,
            "embedding": settings.EMBEDDING_MODEL,
        },
        "version": "0.1.0",
    }
