"""
FastAPI Application Entry Point for Agentic HR Core.
Configures structured JSON logging, Request ID middleware, unified error handling, and API routing.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.api.v1.endpoints.health import router as health_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.core.middleware import RequestIDMiddleware
from app.routers.synthetic import router as synthetic_router

# 1. Initialize structured JSON logging
setup_logging(log_level="INFO" if not settings.DEBUG else "DEBUG")
logger = logging.getLogger("agentic_hr.main")

# 2. Initialize Sentry (production only)
if settings.SENTRY_DSN and settings.ENVIRONMENT == "production":
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=0.1,
        )
        logger.info("Sentry monitoring initialized.")
    except Exception as exc:
        logger.warning(f"Failed to initialize Sentry: {exc}")

# 3. Create FastAPI application instance
app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="Agentic HR Core API - Workforce Automation for SMEs",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 4. Attach Middleware (Request ID middleware must wrap request processing)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 5. Register Unified Error Handlers (Consistent JSON error contract)
register_error_handlers(app)

# 6. Mount Routers
# Primary health probe: GET /health
app.include_router(health_router)
# Backward-compatibility alias: GET /api/health
app.include_router(health_router, prefix="/api")
# V1 API Routes: /api/v1/...
app.include_router(api_router, prefix="/api/v1")
# Preserved Stage 1 Synthetic Routes: /api/synthetic/...
app.include_router(synthetic_router, prefix="/api")


@app.get("/")
async def root() -> dict[str, object]:
    """Root platform metadata endpoint."""
    return {
        "app": settings.PROJECT_NAME,
        "stage": "Stage 1 - Platform Skeleton",
        "docs": "/docs",
        "health": "/health",
        "use_fake_integrations": settings.USE_FAKE_INTEGRATIONS,
    }
