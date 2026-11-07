"""
API V1 router aggregator for Agentic HR Core.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import approvals, audit, auth, health, workflows

api_router = APIRouter()

# Mount health routes under /api/v1
api_router.include_router(health.router)
# Mount workflow routes under /api/v1/workflows
api_router.include_router(workflows.router)
# Mount auth routes under /api/v1/auth
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# Mount audit routes under /api/v1/audit
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
# Mount approval routes under /api/v1/approvals
api_router.include_router(approvals.router, prefix="/approvals", tags=["approvals"])
