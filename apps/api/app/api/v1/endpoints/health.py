"""
Health check endpoints for Agentic HR Core.
Provides system runtime status, database health, and model routing info.
"""

from fastapi import APIRouter, Request

from app.core.config import settings
from app.core.middleware import request_id_ctx
from app.db.session import check_db_health
from app.schemas.health import DatabaseStatus, HealthResponse, ModelRoutingInfo

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check probe",
    description="Returns service status, database connectivity, pgvector presence, and AI model routing.",
)
async def get_health(request: Request) -> HealthResponse:
    request_id = getattr(request.state, "request_id", None) or request_id_ctx.get(None)
    db_health = await check_db_health()

    return HealthResponse(
        status="healthy",
        stage="Stage 3 complete — Stage 4 next",
        environment=settings.ENVIRONMENT,
        version="0.1.0",
        use_fake_integrations=settings.USE_FAKE_INTEGRATIONS,
        database_connected=db_health["connected"],
        database=DatabaseStatus(
            connected=db_health["connected"],
            pgvector_enabled=db_health["pgvector_enabled"],
        ),
        models=ModelRoutingInfo(
            cheap=settings.CHEAP_MODEL,
            strong=settings.STRONG_MODEL,
            embedding=settings.EMBEDDING_MODEL,
        ),
        request_id=request_id,
    )
