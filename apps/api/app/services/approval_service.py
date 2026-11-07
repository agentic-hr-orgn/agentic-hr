"""
Domain Service for Human Approval Workflows with Multi-Round Revisions.
Implements transactional 3-decision state machine (Approve, Request Changes, Reject),
resubmission with append-only revisions, row-level concurrency locking, RBAC enforcement,
anti-self-approval rules, domain recalculations, and audit log integration.
Complies with Stage 2 specifications.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.security import AuthenticatedUserContext
from app.models.approval import Approval
from app.models.approval_revision import ApprovalRevision
from app.schemas.approval import (
    ApprovalCreateRequest,
    ApprovalResubmitRequest,
    validate_approval_payload,
)
from app.services.audit_service import write_audit

logger = logging.getLogger("agentic_hr.approvals")

APPROVAL_EAGER_LOAD = (
    joinedload(Approval.requester),
    joinedload(Approval.decider),
    joinedload(Approval.revisions).joinedload(ApprovalRevision.submitter),
    joinedload(Approval.revisions).joinedload(ApprovalRevision.decider),
)


async def create_approval(
    db: AsyncSession,
    current_user: AuthenticatedUserContext,
    request: ApprovalCreateRequest,
) -> Approval:
    """
    Submits a new human approval request within the active tenant (Revision 1).
    Validates domain payload against the typed schema registry and creates an append-only revision record.
    """
    # 1. Validate domain payload against typed Pydantic schema
    try:
        validated_payload = validate_approval_payload(request.approval_type, request.payload)
    except Exception as exc:
        logger.warning(f"Payload validation failed for {request.approval_type}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid payload for approval_type '{request.approval_type}': {exc}",
        ) from exc

    # 2. Instantiate parent Approval entity (forces tenant_id, requester_id, status=pending, rev=1)
    approval = Approval(
        tenant_id=UUID(current_user.tenant_id),
        approval_type=request.approval_type,
        current_revision=1,
        payload=validated_payload,
        requester_id=UUID(current_user.user_id),
        approver_role=request.approver_role,
        status="pending",
        workflow_id=request.workflow_id,
    )
    db.add(approval)
    await db.flush()  # Populates approval.id

    # 3. Create initial append-only Revision 1 record
    initial_revision = ApprovalRevision(
        tenant_id=UUID(current_user.tenant_id),
        approval_id=approval.id,
        revision_number=1,
        payload=validated_payload,
        submitted_by=UUID(current_user.user_id),
        submitted_at=approval.created_at,
        decision=None,
    )
    db.add(initial_revision)
    await db.flush()

    # 4. Write transactional audit log
    await write_audit(
        session=db,
        tenant_id=UUID(current_user.tenant_id),
        actor_id=UUID(current_user.user_id),
        action="approval.create",
        target_type="approval",
        target_id=str(approval.id),
        details={
            "approval_type": request.approval_type,
            "approver_role": request.approver_role,
            "revision_number": 1,
            "payload": validated_payload,
        },
    )

    # 5. Eagerly load relationships before commit while transaction and RLS context are active
    stmt = select(Approval).options(*APPROVAL_EAGER_LOAD).where(Approval.id == approval.id)
    loaded_approval = (await db.execute(stmt)).unique().scalar_one()

    await db.commit()
    return loaded_approval


async def list_approvals(
    db: AsyncSession,
    current_user: AuthenticatedUserContext,
    status_filter: str | None = None,
    limit: int = 50,
) -> list[Approval]:
    """
    Retrieves approvals for the current tenant, optionally filtered by status.
    Row-Level Security automatically confines results to the active tenant.
    """
    stmt = (
        select(Approval)
        .options(*APPROVAL_EAGER_LOAD)
        .order_by(Approval.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        valid_statuses = {"pending", "changes_requested", "approved", "rejected"}
        if status_filter in valid_statuses:
            stmt = stmt.where(Approval.status == status_filter)

    result = await db.execute(stmt)
    return list(result.unique().scalars().all())


async def get_approval_by_id(
    db: AsyncSession,
    current_user: AuthenticatedUserContext,
    approval_id: UUID,
) -> Approval:
    """
    Retrieves a single approval record by its primary key with loaded relationships and revisions.
    Raises 404 if not found or cross-tenant.
    """
    stmt = select(Approval).options(*APPROVAL_EAGER_LOAD).where(Approval.id == approval_id)
    approval = (await db.execute(stmt)).unique().scalar_one_or_none()
    if not approval:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found.",
        )
    return approval


async def decide_approval(
    db: AsyncSession,
    current_user: AuthenticatedUserContext,
    approval_id: UUID,
    decision: str,
    reason: str | None = None,
) -> Approval:
    """
    Executes a terminal decision (approved or rejected) with row-level locking.
    Enforces terminal state immutability, role permissions, anti-self-approval, and mandatory rejection reason.
    """
    if decision not in ("approved", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid decision '{decision}'. Must be 'approved' or 'rejected'.",
        )

    # 1. Row-level concurrency locking: SELECT ... FOR UPDATE
    stmt = select(Approval).where(Approval.id == approval_id).with_for_update()
    approval = (await db.execute(stmt)).scalar_one_or_none()
    if not approval:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found.",
        )

    # 2. Check terminal state immutability
    if approval.status in ("approved", "rejected"):
        logger.warning(
            f"User {current_user.email} attempted decision on already terminal approval {approval_id} (current status: {approval.status})"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval request has already been decided with status '{approval.status}'.",
        )

    # Cannot decide if approval is in 'changes_requested' (must wait for resubmission)
    if approval.status == "changes_requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot decide an approval awaiting changes. A corrected resubmission is required.",
        )

    # 3. Validate mandatory rejection reason
    clean_reason = reason.strip() if reason else None
    if decision == "rejected" and not clean_reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Decision reason is strictly required when rejecting an approval request.",
        )

    # 4. Check approver authorization
    _verify_approver_authorization(approval, current_user)

    # 5. Anti-self-approval rule: Non-owners cannot decide their own requests
    if (
        approval.requester_id
        and str(approval.requester_id) == str(current_user.user_id)
        and current_user.role != "owner"
    ):
        logger.warning(f"User {current_user.email} attempted to self-decide request {approval_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: requesters cannot decide their own approval requests.",
        )

    # 6. Apply state transition on parent Approval
    previous_status = approval.status
    approval.status = decision
    approval.decided_by = UUID(current_user.user_id)
    approval.decision_reason = clean_reason
    approval.decided_at = datetime.now(UTC)

    # 7. Update active revision round in approval_revisions
    rev_stmt = (
        select(ApprovalRevision)
        .where(
            ApprovalRevision.approval_id == approval.id,
            ApprovalRevision.revision_number == approval.current_revision,
        )
        .with_for_update()
    )
    current_rev = (await db.execute(rev_stmt)).scalar_one_or_none()
    if current_rev:
        current_rev.decision = decision
        current_rev.decided_by = UUID(current_user.user_id)
        current_rev.decision_reason = clean_reason
        current_rev.decided_at = datetime.now(UTC)

    # 8. Write transactional audit log
    await write_audit(
        session=db,
        tenant_id=UUID(current_user.tenant_id),
        actor_id=UUID(current_user.user_id),
        action=f"approval.{decision}",
        target_type="approval",
        target_id=str(approval.id),
        details={
            "decision": decision,
            "decision_reason": clean_reason,
            "previous_status": previous_status,
            "revision_number": approval.current_revision,
            "approval_type": approval.approval_type,
            "approver_role": approval.approver_role,
        },
    )

    # 9. Eagerly reload relationships before commit
    stmt_reload = select(Approval).options(*APPROVAL_EAGER_LOAD).where(Approval.id == approval_id)
    loaded_approval = (await db.execute(stmt_reload)).unique().scalar_one()

    await db.commit()

    # 10. Signal any waiting Temporal workflows
    try:
        from app.core.temporal import signal_approval_workflow

        await signal_approval_workflow(
            approval_id=str(loaded_approval.id),
            action=decision,
            decided_by=str(current_user.user_id),
            tenant_id=str(current_user.tenant_id),
            reason=clean_reason,
            workflow_id=loaded_approval.workflow_id,
        )
    except Exception as exc:
        logger.warning(f"Temporal workflow signaling skipped: {exc}")

    return loaded_approval


async def request_changes_on_approval(
    db: AsyncSession,
    current_user: AuthenticatedUserContext,
    approval_id: UUID,
    reason: str,
) -> Approval:
    """
    Approver requests modifications on a submitted request.
    Requires non-empty correction instructions. Transitions status to 'changes_requested'.
    """
    clean_reason = reason.strip() if reason else ""
    if not clean_reason or len(clean_reason) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Correction instructions (minimum 3 characters) are strictly required when requesting changes.",
        )

    # 1. Row-level concurrency locking
    stmt = select(Approval).where(Approval.id == approval_id).with_for_update()
    approval = (await db.execute(stmt)).scalar_one_or_none()
    if not approval:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found.",
        )

    # 2. Check current status
    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot request changes on approval with status '{approval.status}'. Must be 'pending'.",
        )

    # 3. Check approver authorization
    _verify_approver_authorization(approval, current_user)

    # 4. Anti-self-approval rule
    if (
        approval.requester_id
        and str(approval.requester_id) == str(current_user.user_id)
        and current_user.role != "owner"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: requesters cannot request changes on their own approval requests.",
        )

    # 5. Transition status to changes_requested
    previous_status = approval.status
    approval.status = "changes_requested"
    approval.decided_by = UUID(current_user.user_id)
    approval.decision_reason = clean_reason
    approval.decided_at = datetime.now(UTC)

    # 6. Update current revision round snapshot
    rev_stmt = (
        select(ApprovalRevision)
        .where(
            ApprovalRevision.approval_id == approval.id,
            ApprovalRevision.revision_number == approval.current_revision,
        )
        .with_for_update()
    )
    current_rev = (await db.execute(rev_stmt)).scalar_one_or_none()
    if current_rev:
        current_rev.decision = "changes_requested"
        current_rev.decided_by = UUID(current_user.user_id)
        current_rev.decision_reason = clean_reason
        current_rev.decided_at = datetime.now(UTC)

    # 7. Write transactional audit log
    await write_audit(
        session=db,
        tenant_id=UUID(current_user.tenant_id),
        actor_id=UUID(current_user.user_id),
        action="approval.request_changes",
        target_type="approval",
        target_id=str(approval.id),
        details={
            "revision_number": approval.current_revision,
            "instructions": clean_reason,
            "previous_status": previous_status,
            "approval_type": approval.approval_type,
            "approver_role": approval.approver_role,
        },
    )

    # 8. Eagerly reload relationships before commit
    stmt_reload = select(Approval).options(*APPROVAL_EAGER_LOAD).where(Approval.id == approval_id)
    loaded_approval = (await db.execute(stmt_reload)).unique().scalar_one()

    await db.commit()

    # 9. Signal waiting Temporal workflow (changes_requested)
    try:
        from app.core.temporal import signal_approval_workflow

        await signal_approval_workflow(
            approval_id=str(loaded_approval.id),
            action="changes_requested",
            decided_by=str(current_user.user_id),
            tenant_id=str(current_user.tenant_id),
            reason=clean_reason,
            workflow_id=loaded_approval.workflow_id,
        )
    except Exception as exc:
        logger.warning(f"Temporal workflow signaling skipped: {exc}")

    return loaded_approval


async def resubmit_approval(
    db: AsyncSession,
    current_user: AuthenticatedUserContext,
    approval_id: UUID,
    resubmit_req: ApprovalResubmitRequest,
) -> Approval:
    """
    Requester (or organization owner) resubmits a corrected draft.
    Validates updated payload, increments revision number, stores new append-only revision,
    and returns approval to 'pending' review.
    """
    # 1. Row-level concurrency locking
    stmt = select(Approval).where(Approval.id == approval_id).with_for_update()
    approval = (await db.execute(stmt)).scalar_one_or_none()
    if not approval:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found.",
        )

    # 2. Check current status: must be 'changes_requested'
    if approval.status != "changes_requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot resubmit approval with status '{approval.status}'. Must be 'changes_requested'.",
        )

    # 3. Check editor authorization: original requester or organization owner
    is_requester = approval.requester_id and str(approval.requester_id) == str(current_user.user_id)
    is_owner = current_user.role == "owner"
    if not (is_requester or is_owner):
        logger.warning(
            f"Unauthorized user {current_user.email} attempted to resubmit approval {approval_id} (requester: {approval.requester_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: only the original requester or organization owner may edit and resubmit returned drafts.",
        )

    # 4. Validate updated payload against typed domain schema
    try:
        validated_payload = validate_approval_payload(approval.approval_type, resubmit_req.payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid corrected payload for approval_type '{approval.approval_type}': {exc}",
        ) from exc

    # 5. Increment revision number and update parent Approval
    next_revision = approval.current_revision + 1
    previous_revision = approval.current_revision
    previous_instructions = approval.decision_reason

    approval.current_revision = next_revision
    approval.payload = validated_payload
    approval.status = "pending"
    approval.resubmitted_at = datetime.now(UTC)
    approval.decided_by = None
    approval.decision_reason = None
    approval.decided_at = None

    # 6. Create new append-only Revision round
    new_revision = ApprovalRevision(
        tenant_id=UUID(current_user.tenant_id),
        approval_id=approval.id,
        revision_number=next_revision,
        payload=validated_payload,
        submitted_by=UUID(current_user.user_id),
        submitted_at=datetime.now(UTC),
        decision=None,
    )
    db.add(new_revision)
    await db.flush()

    # 7. Write transactional audit log
    await write_audit(
        session=db,
        tenant_id=UUID(current_user.tenant_id),
        actor_id=UUID(current_user.user_id),
        action="approval.resubmit",
        target_type="approval",
        target_id=str(approval.id),
        details={
            "previous_revision": previous_revision,
            "new_revision": next_revision,
            "previous_instructions": previous_instructions,
            "approval_type": approval.approval_type,
            "payload": validated_payload,
        },
    )

    # 8. Eagerly reload relationships before commit
    stmt_reload = select(Approval).options(*APPROVAL_EAGER_LOAD).where(Approval.id == approval_id)
    loaded_approval = (await db.execute(stmt_reload)).unique().scalar_one()

    await db.commit()

    # 9. Signal waiting Temporal workflow (resubmitted)
    try:
        from app.core.temporal import signal_approval_workflow

        await signal_approval_workflow(
            approval_id=str(loaded_approval.id),
            action="resubmitted",
            decided_by=str(current_user.user_id),
            tenant_id=str(current_user.tenant_id),
            reason=None,
            workflow_id=loaded_approval.workflow_id,
        )
    except Exception as exc:
        logger.warning(f"Temporal workflow signaling skipped: {exc}")

    return loaded_approval


async def get_approval_revisions(
    db: AsyncSession,
    current_user: AuthenticatedUserContext,
    approval_id: UUID,
) -> list[ApprovalRevision]:
    """
    Returns the complete chronological list of revisions for an approval request.
    Tenant isolation is enforced via RLS.
    """
    # Verify approval exists and belongs to active tenant
    await get_approval_by_id(db, current_user, approval_id)

    stmt = (
        select(ApprovalRevision)
        .options(
            joinedload(ApprovalRevision.submitter),
            joinedload(ApprovalRevision.decider),
        )
        .where(ApprovalRevision.approval_id == approval_id)
        .order_by(ApprovalRevision.revision_number.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _verify_approver_authorization(
    approval: Approval, current_user: AuthenticatedUserContext
) -> None:
    """Helper enforcing role and permission flag requirements for deciding approvals."""
    required_role = approval.approver_role
    user_role = current_user.role
    is_authorized = False

    if required_role == "owner":
        is_authorized = user_role == "owner"
    elif required_role == "manager":
        is_authorized = user_role in ("owner", "manager")
    elif required_role == "hr_head":
        is_authorized = current_user.is_hr_head or user_role == "owner"
    elif required_role == "payroll_admin":
        is_authorized = current_user.is_payroll_admin or user_role == "owner"

    if not is_authorized:
        logger.warning(
            f"User {current_user.email} (role: {user_role}, hr_head: {current_user.is_hr_head}, payroll_admin: {current_user.is_payroll_admin}) "
            f"denied decision on approval requiring '{required_role}'"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: deciding this approval requires '{required_role}' role or permission.",
        )
