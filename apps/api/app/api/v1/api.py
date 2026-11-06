"""
API V1 router aggregator for Agentic HR Core.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import health, workflows

api_router = APIRouter()

# Mount health routes under /api/v1
api_router.include_router(health.router)
# Mount workflow routes under /api/v1/workflows
api_router.include_router(workflows.router)
