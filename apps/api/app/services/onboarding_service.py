"""
Onboarding Wizard State, Readiness & Launch Service for Agentic HR Core (Stage 4 Prompt 2).

Key Invariants:
- Resumable per-tenant state across Steps 1..5 with Stage 6 structure_config placeholders
  (`leave_rules`, `weekends` defaulting to `["Saturday", "Sunday"]`, `holidays`).
- Immutable dict/list assignment when updating JSONB columns so SQLAlchemy always flushes changes.
- Pass 2 Transaction Invariant in `launch_tenant`:
  Phase 1 commits `onboarding_state.is_live = True`, `launched_at` (`Asia/Karachi`),
  `audit_logs`, `sheets_sync_queue`, and `outbox_events` BEFORE dispatching external
  employee invitation emails via `EmailDeliveryService.send_email`.
- CRITICAL RLS Transaction Boundary:
  Immediately re-establishes transaction-local `set_tenant_context(...)` after Phase 1 commit
  and after every per-employee commit in Phase 2 so PostgreSQL RLS never fails closed.
"""

import copy
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import (
    IdempotencyConflictError,
    IdempotencyGate,
    IdempotencyPayloadMismatchError,
    compute_payload_hash,
)
from app.core.timezones import iso_pkt, to_karachi
from app.integrations.base import GmailDeliveryError
from app.models.connector import ConnectorCredential
from app.models.department import Department
from app.models.employee import Employee
from app.models.idempotency import IdempotencyRecord
from app.models.onboarding_state import OnboardingState
from app.models.policy_document import PolicyDocument
from app.models.sheets import SheetsSyncQueue
from app.models.tenant import set_tenant_context
from app.models.tenant_entity import Tenant
from app.schemas.onboarding import (
    DEFAULT_LEAVE_RULES,
    DEFAULT_WEEKENDS,
    OnboardingLaunchResponse,
    OnboardingReadinessCounts,
    OnboardingReadinessResponse,
    OnboardingStateResponse,
    OnboardingStepSaveRequest,
)
from app.services.audit_service import write_audit
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    CredentialNotFoundError,
    ReadOnlyConnectorError,
)
from app.services.email_delivery_service import EmailDeliveryService, RecipientBlockedError
from app.services.outbox_service import enqueue_outbox_event
from app.services.sheets_sync_service import SheetsSyncService

logger = logging.getLogger("agentic_hr.services.onboarding")


def _default_structure_config() -> dict[str, Any]:
    """Returns a fresh copy of the default Stage 6 structure_config placeholders."""
    return {
        "leave_rules": copy.deepcopy(DEFAULT_LEAVE_RULES),
        "weekends": list(DEFAULT_WEEKENDS),
        "holidays": [],
    }


def _merge_structure_config(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Produces a brand-new merged structure_config dict preserving Stage 6 placeholders
    (`leave_rules`, `weekends` defaulting to `["Saturday", "Sunday"]`, `holidays`).
    """
    merged = _default_structure_config()
    if isinstance(existing, dict):
        for k, v in existing.items():
            if k == "leave_rules" and isinstance(v, dict):
                merged["leave_rules"] = {**merged["leave_rules"], **copy.deepcopy(v)}
            else:
                merged[k] = copy.deepcopy(v)

    if isinstance(incoming, dict):
        for k, v in incoming.items():
            if k == "leave_rules" and isinstance(v, dict):
                merged["leave_rules"] = {**merged["leave_rules"], **copy.deepcopy(v)}
            elif k == "weekends":
                merged["weekends"] = (
                    list(v) if isinstance(v, list) and v else list(DEFAULT_WEEKENDS)
                )
            elif k == "holidays":
                merged["holidays"] = copy.deepcopy(v) if isinstance(v, list) else []
            else:
                merged[k] = copy.deepcopy(v)

    if not merged.get("weekends"):
        merged["weekends"] = list(DEFAULT_WEEKENDS)
    return merged


class OnboardingService:
    """Orchestrates tenant onboarding wizard progress, readiness checks, and Go-Live launch."""

    @staticmethod
    async def get_or_create_state(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        actor_id: UUID | str | None = None,
    ) -> OnboardingState:
        """Returns the tenant's onboarding_state record, creating defaults at Step 1 if absent."""
        t_id = UUID(str(tenant_id))
        stmt = select(OnboardingState).where(OnboardingState.tenant_id == t_id)
        state = (await session.execute(stmt)).scalar_one_or_none()
        if state:
            if (
                not isinstance(state.structure_config, dict)
                or "weekends" not in state.structure_config
            ):
                state.structure_config = _merge_structure_config(state.structure_config, None)
                await session.flush()
            return state

        actor_uuid = UUID(str(actor_id)) if actor_id else None
        state = OnboardingState(
            tenant_id=t_id,
            current_step=1,
            completed_steps=[],
            business_type=None,
            structure_config=_default_structure_config(),
            is_live=False,
            launched_at=None,
            updated_by=actor_uuid,
        )
        session.add(state)
        await session.flush()
        return state

    @classmethod
    async def save_step(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        step: int,
        payload: OnboardingStepSaveRequest,
        actor_id: UUID | str | None = None,
    ) -> OnboardingStateResponse:
        """
        Validates step 1..5, persists step progress and structure_config (assigning new JSONB
        list/dict instances), updates completed_steps, and writes an immutable audit log entry.
        """
        if step < 1 or step > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid onboarding step '{step}'. Step must be between 1 and 5.",
            )

        t_id = UUID(str(tenant_id))
        state = await cls.get_or_create_state(session, tenant_id=t_id, actor_id=actor_id)

        if payload.business_type is not None:
            clean_bt = payload.business_type.strip()
            state.business_type = clean_bt or None

        # Always assign brand-new dict & list objects so SQLAlchemy flushes JSONB updates
        state.structure_config = _merge_structure_config(
            state.structure_config,
            payload.structure_config,
        )

        existing_steps = [int(s) for s in (state.completed_steps or []) if isinstance(s, int)]
        state.completed_steps = sorted(set(existing_steps + [step]))

        if payload.current_step is not None:
            state.current_step = payload.current_step
        elif payload.advance_step:
            state.current_step = min(5, max(state.current_step, step + 1))

        if actor_id:
            state.updated_by = UUID(str(actor_id))
        state.updated_at = datetime.now(UTC)

        await session.flush()

        await write_audit(
            session,
            action="onboarding.step_saved",
            target_type="onboarding_state",
            target_id=state.id,
            details={
                "step": step,
                "current_step": state.current_step,
                "completed_steps": list(state.completed_steps),
                "business_type": state.business_type,
            },
            actor_id=actor_id,
            tenant_id=t_id,
        )

        return OnboardingStateResponse.model_validate(state)

    @staticmethod
    async def compute_readiness(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
    ) -> OnboardingReadinessResponse:
        """
        Deterministically computes Step 5 readiness checklist:
        - connectors_ok: active Google connector credential exists and status == "connected"
        - policies_ok: COUNT(policy_documents) >= 1
        - departments_ok: COUNT(departments) >= 1
        - employees_ok: COUNT(employees) >= 1
        - can_go_live: all 4 true
        """
        t_id = UUID(str(tenant_id))

        connectors_count = int(
            (
                await session.execute(
                    select(func.count(ConnectorCredential.id)).where(
                        ConnectorCredential.tenant_id == t_id,
                        ConnectorCredential.provider == "google",
                        ConnectorCredential.status == "connected",
                    )
                )
            ).scalar()
            or 0
        )
        policies_count = int(
            (
                await session.execute(
                    select(func.count(PolicyDocument.id)).where(PolicyDocument.tenant_id == t_id)
                )
            ).scalar()
            or 0
        )
        departments_count = int(
            (
                await session.execute(
                    select(func.count(Department.id)).where(Department.tenant_id == t_id)
                )
            ).scalar()
            or 0
        )
        employees_count = int(
            (
                await session.execute(
                    select(func.count(Employee.id)).where(Employee.tenant_id == t_id)
                )
            ).scalar()
            or 0
        )

        connectors_ok = connectors_count >= 1
        policies_ok = policies_count >= 1
        departments_ok = departments_count >= 1
        employees_ok = employees_count >= 1
        can_go_live = connectors_ok and policies_ok and departments_ok and employees_ok

        missing_items: list[str] = []
        if not connectors_ok:
            missing_items.append(
                "Connect an active Google Workspace credential (status must be 'connected')."
            )
        if not policies_ok:
            missing_items.append("Upload at least one corporate policy document (Step 2).")
        if not departments_ok:
            missing_items.append("Create at least one organizational department (Step 3).")
        if not employees_ok:
            missing_items.append("Add at least one employee to the directory (Step 4).")

        return OnboardingReadinessResponse(
            connectors_ok=connectors_ok,
            policies_ok=policies_ok,
            departments_ok=departments_ok,
            employees_ok=employees_ok,
            can_go_live=can_go_live,
            counts=OnboardingReadinessCounts(
                connectors=connectors_count,
                policies=policies_count,
                departments=departments_count,
                employees=employees_count,
            ),
            missing_items=missing_items,
        )

    @classmethod
    async def launch_tenant(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        actor_id: UUID | str,
        clerk_user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> OnboardingLaunchResponse:
        """
        Launches the tenant workspace:
        1. Evaluates readiness; fails with HTTP 400 (ONBOARDING_NOT_READY) if can_go_live is False.
        2. Phase 1 (Domain Transaction): Marks onboarding_state.is_live=True, sets launched_at
           in Asia/Karachi, enqueues employees into sheets_sync_queue, writes outbox & audit log,
           and COMMITS before any external connector call (Pass 2 invariant).
        3. Phase 2 (Post-Commit External Dispatch): Immediately re-establishes transaction-local
           RLS context via set_tenant_context, dispatches per-employee invitation emails with
           idempotency key `onboarding-invite-{tenant_id}-{employee_id}`, re-establishes RLS after
           each commit, and gracefully skips failed/read-only/expired connector sends without
           leaving the launch IdempotencyRecord stuck in 'started'.
        """
        t_id = UUID(str(tenant_id))
        actor_uuid = UUID(str(actor_id))

        # 1. Deterministic Readiness Gate
        readiness = await cls.compute_readiness(session, tenant_id=t_id)
        if not readiness.can_go_live:
            missing_summary = "; ".join(readiness.missing_items)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"ONBOARDING_NOT_READY: Cannot launch tenant. Missing prerequisites: {missing_summary}",
            )

        effective_key = (idempotency_key or "").strip() or None
        payload_hash = compute_payload_hash({"tenant_id": str(t_id), "action": "onboarding.launch"})

        gate_record_id: UUID | None = None
        if effective_key:
            try:
                async with IdempotencyGate(
                    session=session,
                    tenant_id=t_id,
                    operation="onboarding.launch",
                    idempotency_key=effective_key,
                    payload_hash=payload_hash,
                ) as gate:
                    if gate.is_replay:
                        return OnboardingLaunchResponse.model_validate(gate.response_body)
                    gate_record_id = gate.record.id if gate.record else None
                    # Commit 'started' lock together with Phase 1 domain state below
            except IdempotencyPayloadMismatchError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(exc),
                ) from exc
            except IdempotencyConflictError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc

        # ----------------------------------------------------------------------
        # Phase 1: Domain State Mutation, Sheets Queue, Outbox & Audit -> COMMIT
        # ----------------------------------------------------------------------
        state = await cls.get_or_create_state(session, tenant_id=t_id, actor_id=actor_uuid)
        launched_pkt = state.launched_at or to_karachi(datetime.now(UTC))
        state.is_live = True
        state.launched_at = launched_pkt
        state.current_step = 5
        existing_steps = [int(s) for s in (state.completed_steps or []) if isinstance(s, int)]
        state.completed_steps = sorted(set(existing_steps + [5]))
        state.updated_by = actor_uuid
        state.updated_at = datetime.now(UTC)

        # Resolve tenant display name & department names for invitation templates & Sheets payload
        tenant_row = (
            await session.execute(select(Tenant).where(Tenant.id == t_id))
        ).scalar_one_or_none()
        company_name = tenant_row.name if tenant_row else "Agentic HR Workspace"

        dept_rows = (
            (await session.execute(select(Department).where(Department.tenant_id == t_id)))
            .scalars()
            .all()
        )
        dept_names: dict[UUID, str] = {d.id: d.name for d in dept_rows}

        emp_rows = (
            (
                await session.execute(
                    select(Employee)
                    .where(
                        Employee.tenant_id == t_id,
                        Employee.status.in_(["active", "invited", "on_leave"]),
                    )
                    .order_by(Employee.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

        # Check already-queued onboarding launch rows to keep sheets_sync_queue idempotent
        existing_queued_ids = set(
            (
                await session.execute(
                    select(SheetsSyncQueue.entity_id).where(
                        SheetsSyncQueue.tenant_id == t_id,
                        SheetsSyncQueue.entity_type == "employee",
                        SheetsSyncQueue.action == "onboarding_launch",
                    )
                )
            )
            .scalars()
            .all()
        )

        # Snapshot employee metadata before commit so Phase 2 never touches expired ORM objects
        employee_snapshots: list[dict[str, str]] = []
        sheets_queued = 0
        for emp in emp_rows:
            dept_name = (
                dept_names.get(emp.department_id, "General") if emp.department_id else "General"
            )
            emp_id_str = str(emp.id)
            employee_snapshots.append(
                {
                    "id": emp_id_str,
                    "employee_code": emp.employee_code or emp_id_str[:8].upper(),
                    "full_name": emp.full_name,
                    "email": emp.email,
                    "job_title": emp.job_title,
                    "department_name": dept_name,
                    "status": emp.status,
                }
            )
            if emp_id_str not in existing_queued_ids:
                await SheetsSyncService.enqueue_sync(
                    session=session,
                    tenant_id=t_id,
                    entity_type="employee",
                    entity_id=emp_id_str,
                    action="onboarding_launch",
                    payload={
                        "employee_id": emp.employee_code or emp_id_str[:8].upper(),
                        "name": emp.full_name,
                        "email": emp.email,
                        "department": dept_name,
                        "role": emp.job_title,
                        "status": emp.status,
                    },
                )
                sheets_queued += 1

        launched_iso = iso_pkt(launched_pkt) or to_karachi(datetime.now(UTC)).isoformat()

        await enqueue_outbox_event(
            session=session,
            tenant_id=t_id,
            event_type="onboarding.tenant.launched",
            event_payload={
                "tenant_id": str(t_id),
                "company_name": company_name,
                "launched_at": launched_iso,
                "employees_count": len(employee_snapshots),
                "sheets_queued": sheets_queued,
            },
        )

        await write_audit(
            session,
            action="onboarding.launched",
            target_type="onboarding_state",
            target_id=state.id,
            details={
                "is_live": True,
                "launched_at": launched_iso,
                "employees_count": len(employee_snapshots),
                "sheets_queued": sheets_queued,
            },
            actor_id=actor_uuid,
            tenant_id=t_id,
        )

        # Pass 2 Invariant: Commit all domain writes BEFORE any external connector call!
        await session.commit()
        # CRITICAL RLS Transaction Boundary: Re-establish SET LOCAL app.tenant_id after commit
        await set_tenant_context(session, str(t_id), clerk_user_id)

        # ----------------------------------------------------------------------
        # Phase 2: Post-Commit Per-Employee Invitation Dispatch (Idempotent)
        # ----------------------------------------------------------------------
        invitations_sent = 0
        invitations_skipped = 0

        for snap in employee_snapshots:
            emp_invite_key = f"onboarding-invite-{t_id}-{snap['id']}"
            try:
                await EmailDeliveryService.send_email(
                    session=session,
                    tenant_id=t_id,
                    to_email=snap["email"],
                    template_name="onboarding_invitation",
                    template_variables={
                        "company_name": company_name,
                        "employee_name": snap["full_name"],
                        "job_title": snap["job_title"],
                        "department_name": snap["department_name"],
                        "portal_url": "http://localhost:3000/dashboard",
                    },
                    idempotency_key=emp_invite_key,
                    actor_id=actor_uuid,
                )
                await session.commit()
                await set_tenant_context(session, str(t_id), clerk_user_id)
                invitations_sent += 1
            except ConnectorAuthExpiredError:
                # Pass 2 Circuit-Breaker Invariant: persist needs_reconnect + jobs_paused + audit
                await session.commit()
                await set_tenant_context(session, str(t_id), clerk_user_id)
                invitations_skipped += 1
            except GmailDeliveryError:
                # Persist failed SentMessage + audit without aborting launch
                await session.commit()
                await set_tenant_context(session, str(t_id), clerk_user_id)
                invitations_skipped += 1
            except (ReadOnlyConnectorError, RecipientBlockedError, CredentialNotFoundError) as exc:
                logger.warning(
                    f"Skipping onboarding invitation for employee {snap['id']} in tenant {t_id}: {exc}"
                )
                await session.rollback()
                await set_tenant_context(session, str(t_id), clerk_user_id)
                invitations_skipped += 1
            except Exception:
                logger.exception(
                    f"Unexpected error dispatching onboarding invitation for {snap['id']}"
                )
                await session.rollback()
                await set_tenant_context(session, str(t_id), clerk_user_id)
                invitations_skipped += 1

        response = OnboardingLaunchResponse(
            status="launched",
            is_live=True,
            launched_at=launched_iso,
            invitations_sent=invitations_sent,
            invitations_skipped=invitations_skipped,
            sheets_queued=sheets_queued,
            readiness=readiness,
        )

        # Finalize IdempotencyGate record under restored RLS context
        if gate_record_id is not None:
            await set_tenant_context(session, str(t_id), clerk_user_id)
            idem_rec = (
                await session.execute(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.tenant_id == t_id,
                        IdempotencyRecord.id == gate_record_id,
                    )
                )
            ).scalar_one_or_none()
            if idem_rec is not None:
                idem_rec.status = "completed"
                idem_rec.response_code = 200
                idem_rec.response_body = response.model_dump(mode="json")
                await session.commit()
                await set_tenant_context(session, str(t_id), clerk_user_id)

        return response
