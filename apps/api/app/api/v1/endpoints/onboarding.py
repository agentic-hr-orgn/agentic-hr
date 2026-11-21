"""
Onboarding Wizard REST API Endpoints for Agentic HR Core (Stage 4 Prompt 2).

Endpoints:
- GET /api/v1/onboarding/state: Fetch or initialize resumable onboarding wizard state.
- PUT /api/v1/onboarding/steps/{step}: Save step 1..5 progress & Stage 6 structure_config.
- GET /api/v1/onboarding/readiness: Deterministic Step 5 Go-Live readiness checklist.
- POST /api/v1/onboarding/launch: Launch tenant workspace (owner / is_hr_head, Idempotency-Key).
"""

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import RateLimiter
from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_hr_head,
    require_owner_manager_or_hr_head,
)
from app.db.session import get_db
from app.schemas.onboarding import (
    OnboardingLaunchResponse,
    OnboardingReadinessResponse,
    OnboardingStateResponse,
    OnboardingStepSaveRequest,
)
from app.services.onboarding_service import OnboardingService

router = APIRouter()


@router.get("/state", response_model=OnboardingStateResponse)
async def get_onboarding_state(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OnboardingStateResponse:
    """Returns the current tenant's resumable onboarding wizard state."""
    state = await OnboardingService.get_or_create_state(
        session=db,
        tenant_id=current_user.tenant_id,
        actor_id=current_user.user_id,
    )
    await db.commit()
    return OnboardingStateResponse.model_validate(state)


@router.put("/steps/{step}", response_model=OnboardingStateResponse)
async def save_onboarding_step(
    step: int,
    payload: OnboardingStepSaveRequest,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> OnboardingStateResponse:
    """Persists step 1..5 onboarding configuration and advances wizard progress."""
    result = await OnboardingService.save_step(
        session=db,
        tenant_id=current_user.tenant_id,
        step=step,
        payload=payload,
        actor_id=current_user.user_id,
    )
    await db.commit()
    return result


@router.get("/readiness", response_model=OnboardingReadinessResponse)
async def get_onboarding_readiness(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OnboardingReadinessResponse:
    """Deterministically evaluates the Step 5 launch readiness checklist."""
    return await OnboardingService.compute_readiness(
        session=db,
        tenant_id=current_user.tenant_id,
    )


@router.post(
    "/launch",
    response_model=OnboardingLaunchResponse,
    dependencies=[Depends(RateLimiter(requests=30, window_seconds=60, scope="onboarding.launch"))],
)
async def launch_tenant_workspace(
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> OnboardingLaunchResponse:
    """
    Marks the tenant live, queues employee directory Sheets sync, writes audit logs,
    commits domain state BEFORE external connector calls, and dispatches idempotent
    employee invitation emails. Restricted to Owner or HR Head.
    """
    effective_key = idempotency_key or x_idempotency_key
    return await OnboardingService.launch_tenant(
        session=db,
        tenant_id=current_user.tenant_id,
        actor_id=current_user.user_id,
        clerk_user_id=current_user.clerk_user_id,
        idempotency_key=effective_key,
    )
