"""
API V1 router aggregator for Agentic HR Core.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    approvals,
    audit,
    auth,
    connectors,
    departments,
    employees,
    employees_import,
    health,
    notifications,
    onboarding,
    policies,
    tenants,
    workflows,
)

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
# Mount tenant administration routes under /api/v1/tenants
api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
# Mount connector routes under /api/v1/connectors
api_router.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
# Stage 4: Onboarding Wizard, Departments, Policy Upload Stub & Excel Bulk Import routes
# Note: /employees/import is mounted BEFORE any /employees/{employee_id} route in Prompt 4.
api_router.include_router(onboarding.router, prefix="/onboarding", tags=["onboarding"])
api_router.include_router(departments.router, prefix="/departments", tags=["departments"])
api_router.include_router(policies.router, prefix="/policies", tags=["policies"])
api_router.include_router(
    employees_import.router,
    prefix="/employees/import",
    tags=["employees-import"],
)
api_router.include_router(
    employees.router,
    prefix="/employees",
    tags=["employees"],
)
api_router.include_router(
    notifications.router,
    prefix="/notifications",
    tags=["notifications"],
)
