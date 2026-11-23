"""
Helpdesk Ticket, Category Routing & SLA Domain Service for Agentic HR Core (Stage 7 Prompt 2).

Complies with ADR 002 (Forced RLS), ADR 006 (Gmail connector boundaries), ADR 010 (Category
assignment rules, internal note isolation, SLA reminders/escalation, close rating, reopen window,
and `pub_ticket_summary`), and Pass 2 post-commit external side-effect separation.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.common import WorkflowIDReusePolicy

from app.core.config import settings
from app.core.security import AuthenticatedUserContext
from app.core.temporal import get_temporal_client
from app.core.timezones import iso_pkt, to_karachi
from app.integrations.base import GmailDeliveryError
from app.models.employee import Employee
from app.models.membership import Membership
from app.models.role import Role
from app.models.tenant import set_tenant_context
from app.models.ticket import Ticket, TicketAssignmentRule, TicketMessage
from app.models.user import User
from app.schemas.ticket import (
    TicketAssignmentRuleResponse,
    TicketCategory,
    TicketCloseRequest,
    TicketCreateRequest,
    TicketDetailResponse,
    TicketMessageCreateRequest,
    TicketMessageResponse,
    TicketPriority,
    TicketReopenRequest,
    TicketResponse,
    TicketRuleUpdateRequest,
    TicketStatus,
    TicketSummaryResponse,
    TicketUpdateRequest,
)
from app.services.audit_service import write_audit
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    CredentialNotFoundError,
    ReadOnlyConnectorError,
)
from app.services.email_delivery_service import EmailDeliveryService, RecipientBlockedError
from app.services.notification_service import NotificationService
from app.services.outbox_service import enqueue_outbox_event

logger = logging.getLogger("agentic_hr.services.ticket")

DEFAULT_TICKET_RULES: tuple[dict[str, Any], ...] = (
    {
        "category": "hr_policy",
        "default_assignee_role": "hr_head",
        "sla_hours": 24,
        "reminder_before_hours": 4,
        "reopen_window_days": 7,
    },
    {
        "category": "leave_attendance",
        "default_assignee_role": "hr_head",
        "sla_hours": 24,
        "reminder_before_hours": 4,
        "reopen_window_days": 7,
    },
    {
        "category": "payroll",
        "default_assignee_role": "hr_head",
        "sla_hours": 24,
        "reminder_before_hours": 4,
        "reopen_window_days": 7,
    },
    {
        "category": "it_access",
        "default_assignee_role": "owner",
        "sla_hours": 12,
        "reminder_before_hours": 2,
        "reopen_window_days": 7,
    },
    {
        "category": "facilities",
        "default_assignee_role": "manager",
        "sla_hours": 24,
        "reminder_before_hours": 4,
        "reopen_window_days": 7,
    },
    {
        "category": "general",
        "default_assignee_role": "hr_head",
        "sla_hours": 24,
        "reminder_before_hours": 4,
        "reopen_window_days": 7,
    },
)

TEMPORAL_CALL_TIMEOUT_SECONDS = 2.0


def ticket_sla_workflow_id(ticket_id: UUID | str) -> str:
    """Returns the deterministic Temporal workflow ID for a ticket's SLA lifecycle."""
    return f"ticket-sla-{ticket_id}"


def is_hr_or_owner(actor: AuthenticatedUserContext) -> bool:
    """Returns True if the caller holds Owner or HR Head authority."""
    return bool(actor.role == "owner" or actor.is_hr_head)


def is_helpdesk_staff(actor: AuthenticatedUserContext) -> bool:
    """Returns True for Owner, HR Head, or Manager roles allowed to access Helpdesk queues."""
    return bool(actor.role in ("owner", "manager") or actor.is_hr_head)


def _format_pkt_label(dt: datetime | None) -> str:
    if dt is None:
        return "N/A"
    pkt = to_karachi(dt)
    return pkt.strftime("%Y-%m-%d %H:%M PKT")


async def _safe_send_ticket_email(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    clerk_user_id: str | None,
    to_email: str | None,
    template_name: str,
    template_variables: dict[str, Any],
    idempotency_key: str,
    actor_id: UUID | str | None,
) -> dict[str, Any]:
    """
    Post-commit Gmail connector dispatch honoring Pass 2 401 circuit-breaker invariants.
    In `USE_FAKE_INTEGRATIONS=true`, allows `.internal` test addresses so `FakeEmailService`
    deterministically records `sent_messages` rows whenever a connector credential exists.
    """
    clean = (to_email or "").strip().lower()
    if not clean:
        return {"status": "skipped", "reason": "missing_email"}
    if not settings.USE_FAKE_INTEGRATIONS and clean.endswith(".internal"):
        return {"status": "skipped", "reason": "internal_domain_in_live_mode"}

    try:
        res = await EmailDeliveryService.send_email(
            session=db,
            tenant_id=tenant_id,
            to_email=clean,
            template_name=template_name,
            template_variables=template_variables,
            idempotency_key=idempotency_key,
            actor_id=actor_id,
        )
        await db.commit()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return res
    except ConnectorAuthExpiredError as exc:
        await db.commit()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return {"status": "skipped", "reason": "auth_expired", "detail": str(exc)}
    except GmailDeliveryError as exc:
        await db.commit()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return {"status": "skipped", "reason": "delivery_error", "detail": str(exc)}
    except (ReadOnlyConnectorError, CredentialNotFoundError, RecipientBlockedError) as exc:
        await db.rollback()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return {"status": "skipped", "reason": type(exc).__name__, "detail": str(exc)}
    except Exception as exc:
        logger.warning(f"Ticket email dispatch skipped ({idempotency_key}): {exc}")
        await db.rollback()
        await set_tenant_context(db, str(tenant_id), clerk_user_id)
        return {"status": "skipped", "reason": "unexpected_error", "detail": str(exc)}


async def _start_ticket_sla_workflow(
    *,
    tenant_id: UUID,
    ticket_id: UUID,
    reminder_delay_seconds: float,
    breach_delay_seconds: float,
    terminate_existing: bool = False,
) -> bool:
    """
    Starts `TicketSLAWorkflow` under deterministic ID `ticket-sla-{ticket_id}` with a
    short timeout guard so offline Temporal never blocks API handlers or tests.
    """
    wf_id = ticket_sla_workflow_id(ticket_id)
    try:
        from app.workflows.ticket_sla_workflow import (
            TicketSLAWorkflow,
            TicketSLAWorkflowInput,
        )

        client = await asyncio.wait_for(
            get_temporal_client(),
            timeout=TEMPORAL_CALL_TIMEOUT_SECONDS,
        )
        if terminate_existing:
            try:
                existing_handle = client.get_workflow_handle(wf_id)
                await asyncio.wait_for(
                    existing_handle.terminate(reason="Ticket reopened"),
                    timeout=TEMPORAL_CALL_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                logger.debug(f"No running TicketSLAWorkflow to terminate for {wf_id}: {exc}")

        await asyncio.wait_for(
            client.start_workflow(
                TicketSLAWorkflow.run,
                TicketSLAWorkflowInput(
                    tenant_id=str(tenant_id),
                    ticket_id=str(ticket_id),
                    reminder_delay_seconds=max(0.01, float(reminder_delay_seconds)),
                    breach_delay_seconds=max(0.02, float(breach_delay_seconds)),
                ),
                id=wf_id,
                task_queue=settings.TEMPORAL_TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            ),
            timeout=TEMPORAL_CALL_TIMEOUT_SECONDS,
        )
        return True
    except Exception as exc:
        logger.debug(f"Temporal TicketSLAWorkflow start skipped for {wf_id}: {exc}")
        return False


async def _signal_ticket_sla_resolved(
    *,
    ticket_id: UUID,
    resolved_by: str | None,
    target_status: str,
) -> bool:
    """Signals `ticket_resolved` on `ticket-sla-{ticket_id}` with a short timeout guard."""
    wf_id = ticket_sla_workflow_id(ticket_id)
    try:
        client = await asyncio.wait_for(
            get_temporal_client(),
            timeout=TEMPORAL_CALL_TIMEOUT_SECONDS,
        )
        handle = client.get_workflow_handle(wf_id)
        await asyncio.wait_for(
            handle.signal("ticket_resolved", args=[resolved_by, target_status]),
            timeout=TEMPORAL_CALL_TIMEOUT_SECONDS,
        )
        return True
    except Exception as exc:
        logger.debug(f"Temporal TicketSLAWorkflow signal skipped for {wf_id}: {exc}")
        return False


async def _get_user_by_id(db: AsyncSession, user_id: UUID | None) -> User | None:
    if user_id is None:
        return None
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def _resolve_hr_or_owner_user_id(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    exclude_user_id: UUID | None = None,
) -> UUID | None:
    """
    Resolves active HR Head first, falling back to active Owner in the tenant.
    When `exclude_user_id` is supplied (SLA escalation), prefers an HR Head or Owner
    other than the current assignee before falling back to any active Owner/HR Head.
    """
    base_filters = [
        Membership.tenant_id == tenant_id,
        Membership.status == "active",
        or_(Membership.is_hr_head.is_(True), Role.role_key == "owner"),
    ]
    if exclude_user_id is not None:
        other = (
            await db.execute(
                select(Membership.user_id)
                .join(Role, Membership.role_id == Role.id)
                .where(*base_filters, Membership.user_id != exclude_user_id)
                .order_by(Membership.is_hr_head.desc(), Membership.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if other is not None:
            return other

    return (
        await db.execute(
            select(Membership.user_id)
            .join(Role, Membership.role_id == Role.id)
            .where(*base_filters)
            .order_by(Membership.is_hr_head.desc(), Membership.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _resolve_role_user_id(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    role_name: str,
    requester_user_id: UUID,
) -> UUID | None:
    """Resolves an active assignee user_id for `hr_head | owner | manager`."""
    if role_name == "owner":
        owner_id = (
            await db.execute(
                select(Membership.user_id)
                .join(Role, Membership.role_id == Role.id)
                .where(
                    Membership.tenant_id == tenant_id,
                    Membership.status == "active",
                    Role.role_key == "owner",
                )
                .order_by(Membership.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if owner_id is not None:
            return owner_id
        return await _resolve_hr_or_owner_user_id(db, tenant_id)

    if role_name == "manager":
        # Prefer requester's direct reporting manager if linked via Employee.manager_id
        emp = (
            await db.execute(
                select(Employee).where(
                    Employee.tenant_id == tenant_id,
                    Employee.user_id == requester_user_id,
                )
            )
        ).scalar_one_or_none()
        if emp is not None and emp.manager_id is not None:
            mgr_user_id = (
                await db.execute(
                    select(Employee.user_id).where(
                        Employee.tenant_id == tenant_id,
                        Employee.id == emp.manager_id,
                    )
                )
            ).scalar_one_or_none()
            if mgr_user_id is not None:
                return mgr_user_id

        any_mgr = (
            await db.execute(
                select(Membership.user_id)
                .join(Role, Membership.role_id == Role.id)
                .where(
                    Membership.tenant_id == tenant_id,
                    Membership.status == "active",
                    Role.role_key == "manager",
                )
                .order_by(Membership.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if any_mgr is not None:
            return any_mgr

    return await _resolve_hr_or_owner_user_id(db, tenant_id)


async def _resolve_assignee_user_id(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    rule: TicketAssignmentRule,
    requester_user_id: UUID,
    explicit_assignee_user_id: UUID | None = None,
) -> UUID | None:
    if explicit_assignee_user_id is not None:
        active_member = (
            await db.execute(
                select(Membership.user_id).where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id == explicit_assignee_user_id,
                    Membership.status == "active",
                )
            )
        ).scalar_one_or_none()
        if active_member is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Assignee user '{explicit_assignee_user_id}' is not an active member of this tenant.",
            )
        return active_member

    if rule.default_assignee_user_id is not None:
        return rule.default_assignee_user_id

    return await _resolve_role_user_id(
        db,
        tenant_id=tenant_id,
        role_name=rule.default_assignee_role,
        requester_user_id=requester_user_id,
    )


async def _next_ticket_number(db: AsyncSession, *, tenant_id: UUID, year: int) -> str:
    """Atomically generates the next sequential `TKT-{YEAR}-{NNNN}` within `tenant_id`."""
    lock_key = f"ticket-seq:{tenant_id}:{year}"
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key));"),
        {"lock_key": lock_key},
    )
    prefix = f"TKT-{year}-"
    pattern = f"^TKT-{year}-[0-9]+$"
    max_seq = (
        await db.execute(
            text(
                """
                SELECT COALESCE(
                    MAX(CAST(substring(ticket_number FROM '([0-9]+)$') AS INTEGER)),
                    0
                )
                FROM public.tickets
                WHERE tenant_id = :tenant_id
                  AND ticket_number ~ :pattern;
                """
            ),
            {"tenant_id": tenant_id, "pattern": pattern},
        )
    ).scalar_one()
    return f"{prefix}{int(max_seq) + 1:04d}"


async def _enqueue_ticket_outbox(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    event_type: str,
    ticket_id: UUID,
    payload: dict[str, Any],
) -> None:
    await enqueue_outbox_event(
        session=db,
        tenant_id=tenant_id,
        event_type=event_type,
        event_payload={"ticket_id": str(ticket_id), **payload},
    )


class TicketService:
    """Authoritative Helpdesk Ticketing, Assignment Rules, and SLA Service."""

    @staticmethod
    async def ensure_default_rules(
        db: AsyncSession,
        *,
        tenant_id: UUID,
    ) -> list[TicketAssignmentRule]:
        """Idempotently ensures default category assignment rules exist for `tenant_id`."""
        values = [{"tenant_id": tenant_id, **rule} for rule in DEFAULT_TICKET_RULES]
        await db.execute(
            pg_insert(TicketAssignmentRule)
            .values(values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "category"])
        )
        await db.flush()
        rows = (
            (
                await db.execute(
                    select(TicketAssignmentRule)
                    .where(TicketAssignmentRule.tenant_id == tenant_id)
                    .order_by(TicketAssignmentRule.category.asc())
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    @staticmethod
    async def list_rules(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
    ) -> list[TicketAssignmentRuleResponse]:
        if not is_hr_or_owner(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Owner or HR Head may view ticket assignment rules.",
            )
        t_id = UUID(str(actor.tenant_id))
        rules = await TicketService.ensure_default_rules(db, tenant_id=t_id)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)
        return [TicketAssignmentRuleResponse.model_validate(r) for r in rules]

    @staticmethod
    async def upsert_rule(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        category: TicketCategory,
        payload: TicketRuleUpdateRequest,
    ) -> TicketAssignmentRuleResponse:
        if not is_hr_or_owner(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Owner or HR Head may update ticket assignment rules.",
            )
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        await TicketService.ensure_default_rules(db, tenant_id=t_id)

        if payload.default_assignee_user_id is not None:
            member_exists = (
                await db.execute(
                    select(Membership.user_id).where(
                        Membership.tenant_id == t_id,
                        Membership.user_id == payload.default_assignee_user_id,
                        Membership.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if member_exists is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Default assignee user is not an active member of this tenant.",
                )

        rule = (
            await db.execute(
                select(TicketAssignmentRule).where(
                    TicketAssignmentRule.tenant_id == t_id,
                    TicketAssignmentRule.category == category,
                )
            )
        ).scalar_one()

        rule.default_assignee_user_id = payload.default_assignee_user_id
        rule.default_assignee_role = payload.default_assignee_role
        rule.sla_hours = payload.sla_hours
        rule.reminder_before_hours = payload.reminder_before_hours
        rule.reopen_window_days = payload.reopen_window_days
        await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="ticket_rule.updated",
            target_type="ticket_assignment_rule",
            target_id=rule.id,
            details={
                "category": category,
                "default_assignee_role": rule.default_assignee_role,
                "default_assignee_user_id": (
                    str(rule.default_assignee_user_id) if rule.default_assignee_user_id else None
                ),
                "sla_hours": rule.sla_hours,
                "reminder_before_hours": rule.reminder_before_hours,
                "reopen_window_days": rule.reopen_window_days,
            },
        )
        response = TicketAssignmentRuleResponse.model_validate(rule)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)
        return response

    @staticmethod
    async def create_ticket(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        payload: TicketCreateRequest,
    ) -> TicketResponse:
        t_id = UUID(str(actor.tenant_id))
        requester_uuid = UUID(str(actor.user_id))
        await TicketService.ensure_default_rules(db, tenant_id=t_id)

        rule = (
            await db.execute(
                select(TicketAssignmentRule).where(
                    TicketAssignmentRule.tenant_id == t_id,
                    TicketAssignmentRule.category == payload.category,
                )
            )
        ).scalar_one()

        explicit_assignee = payload.assigned_to_user_id if is_helpdesk_staff(actor) else None
        assignee_id = await _resolve_assignee_user_id(
            db,
            tenant_id=t_id,
            rule=rule,
            requester_user_id=requester_uuid,
            explicit_assignee_user_id=explicit_assignee,
        )

        now = datetime.now(UTC)
        pkt_year = to_karachi(now).year
        ticket_number = await _next_ticket_number(db, tenant_id=t_id, year=pkt_year)

        sla_due_at = now + timedelta(hours=int(rule.sla_hours))
        sla_reminder_at = sla_due_at - timedelta(hours=int(rule.reminder_before_hours))
        sla_reminder_at = min(sla_reminder_at, sla_due_at)

        ticket_id = uuid.uuid4()
        ticket = Ticket(
            id=ticket_id,
            tenant_id=t_id,
            ticket_number=ticket_number,
            requester_user_id=requester_uuid,
            assigned_to_user_id=assignee_id,
            category=payload.category,
            priority=payload.priority,
            status="open",
            subject=payload.subject.strip(),
            description=payload.description.strip(),
            source=payload.source,
            source_session_id=payload.source_session_id,
            sla_due_at=sla_due_at,
            sla_reminder_at=sla_reminder_at,
        )
        db.add(ticket)
        await db.flush()

        db.add(
            TicketMessage(
                tenant_id=t_id,
                ticket_id=ticket.id,
                author_user_id=requester_uuid,
                body=payload.description.strip(),
                is_internal=False,
            )
        )

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=requester_uuid,
            action="ticket.created",
            target_type="ticket",
            target_id=ticket.id,
            details={
                "ticket_number": ticket.ticket_number,
                "category": ticket.category,
                "priority": ticket.priority,
                "source": ticket.source,
                "assigned_to_user_id": str(assignee_id) if assignee_id else None,
            },
        )
        await _enqueue_ticket_outbox(
            db,
            tenant_id=t_id,
            event_type="ticket.created",
            ticket_id=ticket.id,
            payload={
                "tenant_id": str(t_id),
                "ticket_number": ticket.ticket_number,
                "category": ticket.category,
                "priority": ticket.priority,
                "source": ticket.source,
                "requester_user_id": str(requester_uuid),
                "assigned_to_user_id": str(assignee_id) if assignee_id else None,
            },
        )

        assignee_user = await _get_user_by_id(db, assignee_id)
        if assignee_user is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=t_id,
                recipient_user_id=assignee_user.id,
                recipient_email=assignee_user.email,
                category="system",
                severity="info",
                title=f"New Ticket {ticket.ticket_number}: {ticket.subject[:120]}",
                message=(
                    f"{actor.name} opened a {ticket.priority} priority {ticket.category} ticket "
                    f"(SLA due {_format_pkt_label(ticket.sla_due_at)})."
                ),
                entity_type="ticket",
                entity_id=ticket.id,
                dedupe_key=f"ticket-created:{ticket.id}",
            )

        response = TicketResponse.model_validate(ticket)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)

        # Post-commit Gmail notification to assignee
        if assignee_user is not None:
            await _safe_send_ticket_email(
                db,
                tenant_id=t_id,
                clerk_user_id=actor.clerk_user_id,
                to_email=assignee_user.email,
                template_name="ticket_created",
                template_variables={
                    "assignee_name": assignee_user.name,
                    "ticket_number": ticket.ticket_number,
                    "requester_name": actor.name,
                    "subject": ticket.subject,
                    "category": ticket.category,
                    "priority": ticket.priority,
                    "sla_due_pkt": _format_pkt_label(ticket.sla_due_at),
                },
                idempotency_key=f"ticket-created:{ticket.id}",
                actor_id=requester_uuid,
            )

        if payload.start_workflow:
            rem_delay = (
                float(payload.reminder_delay_seconds)
                if payload.reminder_delay_seconds is not None
                else max(1.0, (sla_reminder_at - now).total_seconds())
            )
            breach_delay = (
                float(payload.breach_delay_seconds)
                if payload.breach_delay_seconds is not None
                else max(rem_delay + 1.0, (sla_due_at - now).total_seconds())
            )
            await _start_ticket_sla_workflow(
                tenant_id=t_id,
                ticket_id=ticket.id,
                reminder_delay_seconds=rem_delay,
                breach_delay_seconds=breach_delay,
            )

        return response

    @staticmethod
    async def list_my_tickets(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        status_filter: TicketStatus | None = None,
    ) -> list[TicketResponse]:
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        filters: list[Any] = [
            Ticket.tenant_id == t_id,
            Ticket.requester_user_id == actor_uuid,
        ]
        if status_filter is not None:
            filters.append(Ticket.status == status_filter)
        rows = (
            (
                await db.execute(
                    select(Ticket)
                    .where(*filters)
                    .order_by(Ticket.created_at.desc(), Ticket.id.desc())
                )
            )
            .scalars()
            .all()
        )
        return [TicketResponse.model_validate(r) for r in rows]

    @staticmethod
    async def list_staff_queue(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        status_filter: TicketStatus | None = None,
        category_filter: TicketCategory | None = None,
        priority_filter: TicketPriority | None = None,
        assigned_to_user_id: UUID | None = None,
    ) -> list[TicketResponse]:
        if not is_helpdesk_staff(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Helpdesk staff privileges required.",
            )
        t_id = UUID(str(actor.tenant_id))
        filters: list[Any] = [Ticket.tenant_id == t_id]
        if status_filter is not None:
            filters.append(Ticket.status == status_filter)
        if category_filter is not None:
            filters.append(Ticket.category == category_filter)
        if priority_filter is not None:
            filters.append(Ticket.priority == priority_filter)
        if assigned_to_user_id is not None:
            filters.append(Ticket.assigned_to_user_id == assigned_to_user_id)
        rows = (
            (
                await db.execute(
                    select(Ticket)
                    .where(*filters)
                    .order_by(Ticket.sla_due_at.asc(), Ticket.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [TicketResponse.model_validate(r) for r in rows]

    @staticmethod
    async def _get_ticket_or_404(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
    ) -> Ticket:
        ticket = (
            await db.execute(
                select(Ticket).where(
                    Ticket.tenant_id == tenant_id,
                    Ticket.id == ticket_id,
                )
            )
        ).scalar_one_or_none()
        if ticket is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ticket '{ticket_id}' not found.",
            )
        return ticket

    @staticmethod
    async def get_ticket_detail(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        ticket_id: UUID,
    ) -> TicketDetailResponse:
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        ticket = await TicketService._get_ticket_or_404(db, tenant_id=t_id, ticket_id=ticket_id)

        staff = is_helpdesk_staff(actor)
        if not staff and ticket.requester_user_id != actor_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: you may only view your own tickets.",
            )

        msg_filters: list[Any] = [
            TicketMessage.tenant_id == t_id,
            TicketMessage.ticket_id == ticket.id,
        ]
        if not staff:
            msg_filters.append(TicketMessage.is_internal.is_(False))

        messages = (
            (
                await db.execute(
                    select(TicketMessage)
                    .where(*msg_filters)
                    .order_by(TicketMessage.created_at.asc(), TicketMessage.id.asc())
                )
            )
            .scalars()
            .all()
        )
        base = TicketResponse.model_validate(ticket).model_dump()
        base["messages"] = [TicketMessageResponse.model_validate(m) for m in messages]
        return TicketDetailResponse(**base)

    @staticmethod
    async def update_ticket(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        ticket_id: UUID,
        payload: TicketUpdateRequest,
    ) -> TicketResponse:
        if not is_helpdesk_staff(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Helpdesk staff may update ticket status, priority, or assignment.",
            )
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        await TicketService.ensure_default_rules(db, tenant_id=t_id)

        ticket = await TicketService._get_ticket_or_404(db, tenant_id=t_id, ticket_id=ticket_id)
        prev_status = ticket.status
        now = datetime.now(UTC)

        if payload.category is not None:
            ticket.category = payload.category
        if payload.priority is not None:
            ticket.priority = payload.priority
        if payload.assigned_to_user_id is not None:
            active_member = (
                await db.execute(
                    select(Membership.user_id).where(
                        Membership.tenant_id == t_id,
                        Membership.user_id == payload.assigned_to_user_id,
                        Membership.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if active_member is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Assigned user is not an active member of this tenant.",
                )
            ticket.assigned_to_user_id = active_member

        transitioned_terminal = False
        if payload.status is not None and payload.status != prev_status:
            ticket.status = payload.status
            if payload.status in ("resolved", "closed"):
                rule = (
                    await db.execute(
                        select(TicketAssignmentRule).where(
                            TicketAssignmentRule.tenant_id == t_id,
                            TicketAssignmentRule.category == ticket.category,
                        )
                    )
                ).scalar_one()
                if ticket.resolved_at is None:
                    ticket.resolved_at = now
                if ticket.reopen_until is None:
                    ticket.reopen_until = ticket.resolved_at + timedelta(
                        days=int(rule.reopen_window_days)
                    )
                if payload.status == "closed" and ticket.closed_at is None:
                    ticket.closed_at = now
                transitioned_terminal = True

        await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="ticket.updated",
            target_type="ticket",
            target_id=ticket.id,
            details={
                "ticket_number": ticket.ticket_number,
                "previous_status": prev_status,
                "status": ticket.status,
                "priority": ticket.priority,
                "category": ticket.category,
                "assigned_to_user_id": (
                    str(ticket.assigned_to_user_id) if ticket.assigned_to_user_id else None
                ),
            },
        )

        requester_user = await _get_user_by_id(db, ticket.requester_user_id)
        if transitioned_terminal:
            event_type = "ticket.closed" if ticket.status == "closed" else "ticket.resolved"
            await _enqueue_ticket_outbox(
                db,
                tenant_id=t_id,
                event_type=event_type,
                ticket_id=ticket.id,
                payload={
                    "tenant_id": str(t_id),
                    "ticket_number": ticket.ticket_number,
                    "status": ticket.status,
                    "reopen_until": iso_pkt(ticket.reopen_until),
                },
            )
            if requester_user is not None:
                await NotificationService.create_deduped(
                    db,
                    tenant_id=t_id,
                    recipient_user_id=requester_user.id,
                    recipient_email=requester_user.email,
                    category="system",
                    severity="info",
                    title=f"Ticket {ticket.ticket_number} {ticket.status.title()}",
                    message=(
                        f"Your ticket '{ticket.subject}' is now {ticket.status}. "
                        f"You may reopen it until {_format_pkt_label(ticket.reopen_until)}."
                    ),
                    entity_type="ticket",
                    entity_id=ticket.id,
                    dedupe_key=f"ticket-status:{ticket.id}:{ticket.status}:{int(now.timestamp())}",
                )

        response = TicketResponse.model_validate(ticket)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)

        if transitioned_terminal:
            if requester_user is not None:
                await _safe_send_ticket_email(
                    db,
                    tenant_id=t_id,
                    clerk_user_id=actor.clerk_user_id,
                    to_email=requester_user.email,
                    template_name="ticket_status_updated",
                    template_variables={
                        "requester_name": requester_user.name,
                        "ticket_number": ticket.ticket_number,
                        "subject": ticket.subject,
                        "status_label": ticket.status.upper(),
                        "reopen_until_pkt": _format_pkt_label(ticket.reopen_until),
                    },
                    idempotency_key=f"ticket-status:{ticket.id}:{ticket.status}:{int(now.timestamp())}",
                    actor_id=actor_uuid,
                )
            await _signal_ticket_sla_resolved(
                ticket_id=ticket.id,
                resolved_by=str(actor_uuid),
                target_status=ticket.status,
            )

        return response

    @staticmethod
    async def add_message(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        ticket_id: UUID,
        payload: TicketMessageCreateRequest,
    ) -> TicketMessageResponse:
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        ticket = await TicketService._get_ticket_or_404(db, tenant_id=t_id, ticket_id=ticket_id)

        staff = is_helpdesk_staff(actor)
        if not staff and ticket.requester_user_id != actor_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: you may only reply to your own tickets.",
            )

        # Plain employees can NEVER post internal notes
        effective_internal = bool(payload.is_internal) if staff else False
        clean_body = payload.body.strip()

        msg = TicketMessage(
            tenant_id=t_id,
            ticket_id=ticket.id,
            author_user_id=actor_uuid,
            body=clean_body,
            is_internal=effective_internal,
        )
        db.add(msg)
        await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="ticket.internal_note_added" if effective_internal else "ticket.message_added",
            target_type="ticket",
            target_id=ticket.id,
            details={
                "ticket_number": ticket.ticket_number,
                "message_id": str(msg.id),
                "is_internal": effective_internal,
            },
        )

        # Never notify or email requester when staff posts an internal note
        notify_requester = (not effective_internal) and (actor_uuid != ticket.requester_user_id)
        requester_user = (
            await _get_user_by_id(db, ticket.requester_user_id) if notify_requester else None
        )
        if notify_requester and requester_user is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=t_id,
                recipient_user_id=requester_user.id,
                recipient_email=requester_user.email,
                category="system",
                severity="info",
                title=f"New Reply on {ticket.ticket_number}",
                message=f"{actor.name} replied on '{ticket.subject}': {clean_body[:160]}",
                entity_type="ticket",
                entity_id=ticket.id,
                dedupe_key=f"ticket-reply:{msg.id}",
            )

        response = TicketMessageResponse.model_validate(msg)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)

        if notify_requester and requester_user is not None:
            await _safe_send_ticket_email(
                db,
                tenant_id=t_id,
                clerk_user_id=actor.clerk_user_id,
                to_email=requester_user.email,
                template_name="ticket_public_reply",
                template_variables={
                    "requester_name": requester_user.name,
                    "ticket_number": ticket.ticket_number,
                    "subject": ticket.subject,
                    "author_name": actor.name,
                    "reply_preview": clean_body[:400],
                },
                idempotency_key=f"ticket-reply:{msg.id}",
                actor_id=actor_uuid,
            )

        return response

    @staticmethod
    async def rate_ticket(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        ticket_id: UUID,
        payload: TicketCloseRequest,
    ) -> TicketResponse:
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        ticket = await TicketService._get_ticket_or_404(db, tenant_id=t_id, ticket_id=ticket_id)

        if ticket.requester_user_id != actor_uuid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the ticket requester may submit a close satisfaction rating.",
            )
        if ticket.status not in ("resolved", "closed"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TICKET_NOT_RESOLVED",
                    "message": "Ticket must be resolved or closed before rating.",
                },
            )
        if payload.satisfaction_rating is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="satisfaction_rating (1..5) is required.",
            )

        now = datetime.now(UTC)
        ticket.satisfaction_rating = payload.satisfaction_rating
        ticket.satisfaction_comment = payload.satisfaction_comment
        if ticket.status == "resolved":
            ticket.status = "closed"
            if ticket.closed_at is None:
                ticket.closed_at = now
            # Preserve existing resolved_at and reopen_until

        await db.flush()
        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="ticket.rated",
            target_type="ticket",
            target_id=ticket.id,
            details={
                "ticket_number": ticket.ticket_number,
                "satisfaction_rating": ticket.satisfaction_rating,
                "status": ticket.status,
            },
        )
        response = TicketResponse.model_validate(ticket)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)
        return response

    @staticmethod
    async def reopen_ticket(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        ticket_id: UUID,
        payload: TicketReopenRequest,
    ) -> TicketResponse:
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        await TicketService.ensure_default_rules(db, tenant_id=t_id)

        ticket = await TicketService._get_ticket_or_404(db, tenant_id=t_id, ticket_id=ticket_id)
        if ticket.requester_user_id != actor_uuid and not is_hr_or_owner(actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the requester, HR Head, or Owner may reopen this ticket.",
            )
        if ticket.status not in ("resolved", "closed"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TICKET_NOT_RESOLVED",
                    "message": "Only resolved or closed tickets can be reopened.",
                },
            )

        now = datetime.now(UTC)
        if ticket.reopen_until is None or now > ticket.reopen_until:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TICKET_REOPEN_WINDOW_EXPIRED",
                    "message": "The reopen window for this ticket has expired.",
                },
            )

        rule = (
            await db.execute(
                select(TicketAssignmentRule).where(
                    TicketAssignmentRule.tenant_id == t_id,
                    TicketAssignmentRule.category == ticket.category,
                )
            )
        ).scalar_one()

        sla_due_at = now + timedelta(hours=int(rule.sla_hours))
        sla_reminder_at = sla_due_at - timedelta(hours=int(rule.reminder_before_hours))
        sla_reminder_at = min(sla_reminder_at, sla_due_at)

        ticket.status = "open"
        ticket.resolved_at = None
        ticket.closed_at = None
        ticket.reopen_until = None
        ticket.sla_due_at = sla_due_at
        ticket.sla_reminder_at = sla_reminder_at
        ticket.sla_reminder_sent_at = None
        ticket.sla_escalated_at = None

        reopen_msg = TicketMessage(
            tenant_id=t_id,
            ticket_id=ticket.id,
            author_user_id=actor_uuid,
            body=f"[Reopened] {payload.reason.strip()}",
            is_internal=False,
        )
        db.add(reopen_msg)
        await db.flush()

        await write_audit(
            db,
            tenant_id=t_id,
            actor_id=actor_uuid,
            action="ticket.reopened",
            target_type="ticket",
            target_id=ticket.id,
            details={
                "ticket_number": ticket.ticket_number,
                "reason": payload.reason.strip(),
            },
        )
        await _enqueue_ticket_outbox(
            db,
            tenant_id=t_id,
            event_type="ticket.reopened",
            ticket_id=ticket.id,
            payload={
                "tenant_id": str(t_id),
                "ticket_number": ticket.ticket_number,
                "reason": payload.reason.strip(),
            },
        )

        assignee_user = await _get_user_by_id(db, ticket.assigned_to_user_id)
        if assignee_user is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=t_id,
                recipient_user_id=assignee_user.id,
                recipient_email=assignee_user.email,
                category="system",
                severity="warning",
                title=f"Ticket Reopened: {ticket.ticket_number}",
                message=f"{actor.name} reopened '{ticket.subject}': {payload.reason.strip()[:160]}",
                entity_type="ticket",
                entity_id=ticket.id,
                dedupe_key=f"ticket-reopened:{reopen_msg.id}",
            )

        response = TicketResponse.model_validate(ticket)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)

        if payload.start_workflow:
            rem_delay = (
                float(payload.reminder_delay_seconds)
                if payload.reminder_delay_seconds is not None
                else max(1.0, (sla_reminder_at - now).total_seconds())
            )
            breach_delay = (
                float(payload.breach_delay_seconds)
                if payload.breach_delay_seconds is not None
                else max(rem_delay + 1.0, (sla_due_at - now).total_seconds())
            )
            await _start_ticket_sla_workflow(
                tenant_id=t_id,
                ticket_id=ticket.id,
                reminder_delay_seconds=rem_delay,
                breach_delay_seconds=breach_delay,
                terminate_existing=True,
            )

        return response

    @staticmethod
    async def list_ticket_summary(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        status_filter: TicketStatus | None = None,
        category_filter: TicketCategory | None = None,
    ) -> list[TicketSummaryResponse]:
        """Reads tenant-isolated projections from secure view `public.pub_ticket_summary`."""
        t_id = UUID(str(actor.tenant_id))
        actor_uuid = UUID(str(actor.user_id))
        clauses = ["tenant_id = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": t_id}

        if not is_helpdesk_staff(actor):
            clauses.append("requester_user_id = :requester_user_id")
            params["requester_user_id"] = actor_uuid
        if status_filter is not None:
            clauses.append("status = :status_filter")
            params["status_filter"] = status_filter
        if category_filter is not None:
            clauses.append("category = :category_filter")
            params["category_filter"] = category_filter

        where_sql = " AND ".join(clauses)
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT
                        ticket_id, tenant_id, ticket_number,
                        requester_user_id, requester_name,
                        assigned_to_user_id, assignee_name,
                        category, priority, status, subject, source,
                        sla_due_at, sla_reminder_at, sla_escalated_at,
                        is_sla_breached, public_message_count,
                        resolved_at, closed_at, satisfaction_rating,
                        created_at, updated_at
                    FROM public.pub_ticket_summary
                    WHERE {where_sql}
                    ORDER BY created_at DESC, ticket_id DESC;
                    """
                ),
                params,
            )
        ).mappings()
        return [TicketSummaryResponse.model_validate(dict(r)) for r in rows]

    @staticmethod
    async def send_sla_reminder(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
    ) -> dict[str, Any]:
        """Idempotent Temporal activity handler for pre-breach SLA reminder."""
        ticket = (
            await db.execute(
                select(Ticket)
                .where(Ticket.tenant_id == tenant_id, Ticket.id == ticket_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if ticket is None:
            return {"status": "not_found"}
        if ticket.status in ("resolved", "closed"):
            return {"status": "skipped_resolved", "ticket_status": ticket.status}
        if ticket.sla_reminder_sent_at is not None:
            return {"status": "already_sent"}

        now = datetime.now(UTC)
        ticket.sla_reminder_sent_at = now
        await db.flush()

        assignee_id = ticket.assigned_to_user_id or await _resolve_hr_or_owner_user_id(
            db, tenant_id
        )
        assignee_user = await _get_user_by_id(db, assignee_id)

        await write_audit(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            action="ticket.sla_reminder_sent",
            target_type="ticket",
            target_id=ticket.id,
            details={
                "ticket_number": ticket.ticket_number,
                "assigned_to_user_id": str(assignee_id) if assignee_id else None,
            },
        )
        if assignee_user is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=tenant_id,
                recipient_user_id=assignee_user.id,
                recipient_email=assignee_user.email,
                category="system",
                severity="warning",
                title=f"SLA Reminder: {ticket.ticket_number}",
                message=(
                    f"Ticket '{ticket.subject}' is nearing its SLA deadline "
                    f"({_format_pkt_label(ticket.sla_due_at)})."
                ),
                entity_type="ticket",
                entity_id=ticket.id,
                dedupe_key=f"ticket-sla-reminder:{ticket.id}",
            )

        await db.commit()
        await set_tenant_context(db, str(tenant_id))

        email_res: dict[str, Any] = {"status": "skipped", "reason": "no_assignee"}
        if assignee_user is not None:
            email_res = await _safe_send_ticket_email(
                db,
                tenant_id=tenant_id,
                clerk_user_id=None,
                to_email=assignee_user.email,
                template_name="ticket_sla_reminder",
                template_variables={
                    "assignee_name": assignee_user.name,
                    "ticket_number": ticket.ticket_number,
                    "subject": ticket.subject,
                    "sla_due_pkt": _format_pkt_label(ticket.sla_due_at),
                },
                idempotency_key=f"ticket-sla-reminder:{ticket.id}",
                actor_id=None,
            )

        return {
            "status": "reminded",
            "ticket_id": str(ticket.id),
            "email_status": email_res.get("status"),
        }

    @staticmethod
    async def escalate_sla_breach(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        ticket_id: UUID,
    ) -> dict[str, Any]:
        """Idempotent Temporal activity handler for SLA breach escalation to HR Head / Owner."""
        ticket = (
            await db.execute(
                select(Ticket)
                .where(Ticket.tenant_id == tenant_id, Ticket.id == ticket_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if ticket is None:
            return {"status": "not_found"}
        if ticket.status in ("resolved", "closed"):
            return {"status": "skipped_resolved", "ticket_status": ticket.status}
        if ticket.sla_escalated_at is not None:
            return {"status": "already_escalated"}

        now = datetime.now(UTC)
        escalation_user_id = await _resolve_hr_or_owner_user_id(
            db,
            tenant_id,
            exclude_user_id=ticket.assigned_to_user_id,
        )
        ticket.sla_escalated_at = now
        if escalation_user_id is not None:
            ticket.assigned_to_user_id = escalation_user_id
        await db.flush()

        escalation_user = await _get_user_by_id(db, escalation_user_id)

        await write_audit(
            db,
            tenant_id=tenant_id,
            actor_id=None,
            action="ticket.sla_breached",
            target_type="ticket",
            target_id=ticket.id,
            details={
                "ticket_number": ticket.ticket_number,
                "escalated_to_user_id": str(escalation_user_id) if escalation_user_id else None,
            },
        )
        await _enqueue_ticket_outbox(
            db,
            tenant_id=tenant_id,
            event_type="ticket.sla_breached",
            ticket_id=ticket.id,
            payload={
                "tenant_id": str(tenant_id),
                "ticket_number": ticket.ticket_number,
                "category": ticket.category,
                "priority": ticket.priority,
                "escalated_to_user_id": str(escalation_user_id) if escalation_user_id else None,
            },
        )
        if escalation_user is not None:
            await NotificationService.create_deduped(
                db,
                tenant_id=tenant_id,
                recipient_user_id=escalation_user.id,
                recipient_email=escalation_user.email,
                category="system",
                severity="critical",
                title=f"SLA Breached: {ticket.ticket_number}",
                message=(
                    f"Ticket '{ticket.subject}' ({ticket.category}, {ticket.priority}) "
                    f"breached SLA at {_format_pkt_label(ticket.sla_due_at)} and was escalated to you."
                ),
                entity_type="ticket",
                entity_id=ticket.id,
                dedupe_key=f"ticket-sla-breach:{ticket.id}",
            )

        await db.commit()
        await set_tenant_context(db, str(tenant_id))

        email_res: dict[str, Any] = {"status": "skipped", "reason": "no_escalation_target"}
        if escalation_user is not None:
            email_res = await _safe_send_ticket_email(
                db,
                tenant_id=tenant_id,
                clerk_user_id=None,
                to_email=escalation_user.email,
                template_name="ticket_sla_escalated",
                template_variables={
                    "escalation_target_name": escalation_user.name,
                    "ticket_number": ticket.ticket_number,
                    "subject": ticket.subject,
                    "category": ticket.category,
                    "priority": ticket.priority,
                    "sla_due_pkt": _format_pkt_label(ticket.sla_due_at),
                },
                idempotency_key=f"ticket-sla-breach:{ticket.id}",
                actor_id=None,
            )

        return {
            "status": "escalated",
            "ticket_id": str(ticket.id),
            "escalated_to_user_id": str(escalation_user_id) if escalation_user_id else None,
            "email_status": email_res.get("status"),
        }
