"""
FastAPI Endpoints for Human Approval System with Multi-Round Revisions.
Exposes approval creation, listing, detail retrieval, 3-decision transitions (approve, request-changes, reject),
draft resubmission, and append-only revision history.
Complies with Stage 2 specifications.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUserContext, get_current_user
from app.db.session import get_db
from app.models.approval import Approval
from app.models.approval_revision import ApprovalRevision
from app.schemas.approval import (
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ApprovalRequestChangesRequest,
    ApprovalResponse,
    ApprovalResubmitRequest,
    ApprovalRevisionResponse,
)
from app.services import approval_service

router = APIRouter()


def _serialize_revision(r: ApprovalRevision) -> ApprovalRevisionResponse:
    """Converts ApprovalRevision ORM entity into ApprovalRevisionResponse DTO."""
    return ApprovalRevisionResponse(
        id=str(r.id),
        approval_id=str(r.approval_id),
        revision_number=r.revision_number,
        payload=r.payload,
        submitted_by=str(r.submitted_by) if r.submitted_by else None,
        submitter_name=r.submitter.name if r.submitter else None,
        submitted_at=r.submitted_at.isoformat(),
        decision=r.decision,
        decided_by=str(r.decided_by) if r.decided_by else None,
        decider_name=r.decider.name if r.decider else None,
        decision_reason=r.decision_reason,
        decided_at=r.decided_at.isoformat() if r.decided_at else None,
        created_at=r.created_at.isoformat(),
    )


def _serialize_approval(a: Approval) -> ApprovalResponse:
    """Converts Approval ORM entity into ApprovalResponse DTO with loaded user and revision info."""
    serialized_revisions = (
        [_serialize_revision(r) for r in a.revisions]
        if hasattr(a, "revisions") and a.revisions
        else []
    )
    return ApprovalResponse(
        id=str(a.id),
        tenant_id=str(a.tenant_id),
        approval_type=a.approval_type,
        current_revision=a.current_revision,
        payload=a.payload,
        requester_id=str(a.requester_id) if a.requester_id else None,
        requester_name=a.requester.name if a.requester else None,
        requester_email=a.requester.email if a.requester else None,
        approver_role=a.approver_role,
        status=a.status,
        decided_by=str(a.decided_by) if a.decided_by else None,
        decider_name=a.decider.name if a.decider else None,
        decision_reason=a.decision_reason,
        created_at=a.created_at.isoformat(),
        decided_at=a.decided_at.isoformat() if a.decided_at else None,
        resubmitted_at=a.resubmitted_at.isoformat() if a.resubmitted_at else None,
        revisions=serialized_revisions,
    )


@router.post(
    "",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a human approval request (Revision 1)",
)
async def create_approval_request(
    request: ApprovalCreateRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """
    Submits a new human approval request.
    Payload is validated against the typed schema for the given approval_type.
    Creates both parent Approval and initial append-only Revision 1 record.
    """
    approval = await approval_service.create_approval(db, current_user, request)
    return _serialize_approval(approval)


@router.get(
    "",
    response_model=list[ApprovalResponse],
    summary="List approvals for current tenant",
)
async def list_approval_requests(
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by status: pending, changes_requested, approved, rejected",
    ),
    limit: int = Query(50, ge=1, le=100),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApprovalResponse]:
    """
    Returns approvals for the authenticated user's active tenant.
    Results are strictly scoped by PostgreSQL Row-Level Security.
    """
    approvals = await approval_service.list_approvals(
        db, current_user, status_filter=status_filter, limit=limit
    )
    return [_serialize_approval(a) for a in approvals]


@router.get(
    "/{approval_id}",
    response_model=ApprovalResponse,
    summary="Get approval request details with revision history",
)
async def get_approval_details(
    approval_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """
    Returns full details of an approval request, including all revision snapshots.
    Cross-tenant requests return 404 Not Found.
    """
    approval = await approval_service.get_approval_by_id(db, current_user, approval_id)
    return _serialize_approval(approval)


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalResponse,
    summary="Approve an approval request (Terminal)",
)
async def approve_request(
    approval_id: UUID,
    decision_req: ApprovalDecisionRequest | None = None,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """
    Approves an approval request with row-level locking.
    Caller must hold the required approver role or permission flag.
    Anti-self-approval rule prevents requesters from approving their own requests.
    """
    reason = decision_req.reason if decision_req else None
    approval = await approval_service.decide_approval(
        db, current_user, approval_id, decision="approved", reason=reason
    )
    return _serialize_approval(approval)


@router.post(
    "/{approval_id}/request-changes",
    response_model=ApprovalResponse,
    summary="Request corrections on an approval request",
)
async def request_changes(
    approval_id: UUID,
    request: ApprovalRequestChangesRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """
    Approver requests modifications on a pending request.
    Non-empty correction instructions are strictly required.
    Transitions status to 'changes_requested' and returns draft to requester.
    """
    approval = await approval_service.request_changes_on_approval(
        db, current_user, approval_id, reason=request.reason
    )
    return _serialize_approval(approval)


@router.post(
    "/{approval_id}/reject",
    response_model=ApprovalResponse,
    summary="Reject an approval request (Terminal)",
)
async def reject_request(
    approval_id: UUID,
    decision_req: ApprovalDecisionRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """
    Rejects an approval request with row-level locking.
    A non-empty rejection reason is strictly required.
    """
    approval = await approval_service.decide_approval(
        db, current_user, approval_id, decision="rejected", reason=decision_req.reason
    )
    return _serialize_approval(approval)


@router.post(
    "/{approval_id}/resubmit",
    response_model=ApprovalResponse,
    summary="Resubmit corrected draft (Round N + 1)",
)
async def resubmit_corrected_draft(
    approval_id: UUID,
    request: ApprovalResubmitRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """
    Requester (or organization owner) resubmits a corrected draft.
    Validates updated payload against domain schema, creates an append-only revision record,
    and returns approval to 'pending' review with an incremented revision number.
    """
    approval = await approval_service.resubmit_approval(db, current_user, approval_id, request)
    return _serialize_approval(approval)


@router.get(
    "/{approval_id}/revisions",
    response_model=list[ApprovalRevisionResponse],
    summary="Get full chronological revision history for an approval request",
)
async def get_approval_revisions(
    approval_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApprovalRevisionResponse]:
    """
    Returns the complete list of revision rounds with payloads and decisions.
    Strictly isolated by PostgreSQL Row-Level Security.
    """
    revisions = await approval_service.get_approval_revisions(db, current_user, approval_id)
    return [_serialize_revision(r) for r in revisions]
