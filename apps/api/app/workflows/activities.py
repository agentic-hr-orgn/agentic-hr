"""
Temporal Activities for Agentic HR Core.
Encapsulates durable execution tasks using fake integration adapters (Gmail, Calendar, Drive, Sheets).
Enforces ADR 006: strictly zero real external API calls under USE_FAKE_INTEGRATIONS=true.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.integrations.base import CalendarEvent, EmailMessage, GmailDeliveryError
from app.integrations.factory import get_calendar_service, get_email_service
from app.models.approval import Approval
from app.models.tenant import set_tenant_context
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    CredentialNotFoundError,
    ReadOnlyConnectorError,
)
from app.services.document_expiry_service import DocumentExpiryService
from app.services.email_delivery_service import EmailDeliveryService, RecipientBlockedError


def _require_fake_integrations(activity_name: str) -> None:
    """
    Fail-closed guard preventing direct Stage 1/2 activity adapters from bypassing
    execute_connector_call when USE_FAKE_INTEGRATIONS=false.
    Stage 4+ workflows must route live dispatches through EmailDeliveryService /
    CalendarSchedulingService with tenant-scoped sessions.
    """
    if not settings.USE_FAKE_INTEGRATIONS:
        raise ApplicationError(
            f"Activity '{activity_name}' cannot call raw integration clients when "
            "USE_FAKE_INTEGRATIONS=false; it must route through EmailDeliveryService / "
            "CalendarSchedulingService (execute_connector_call).",
            non_retryable=True,
        )


@activity.defn
async def greeting_activity(name: str) -> str:
    """Simple activity returning a formatted workflow greeting."""
    activity.logger.info(f"Executing greeting_activity for: {name}")
    return f"Hello from Agentic HR Temporal Workflow, {name}!"


@activity.defn
async def send_welcome_notification(candidate_name: str, candidate_email: str) -> dict[str, Any]:
    """
    Sends an automated onboarding / candidate acknowledgement notification.
    Routes strictly through FakeGmailService (simulated dispatch).
    """
    _require_fake_integrations("send_welcome_notification")
    activity.logger.info(f"Executing send_welcome_notification to {candidate_email}")
    email_service = get_email_service()

    message = EmailMessage(
        to_email=candidate_email,
        subject="Welcome to Agentic HR Platform - Application Received",
        body_text=(
            f"Dear {candidate_name},\n\n"
            "Thank you for submitting your profile. Your application has been ingested "
            "into our autonomous recruiting workflow (DocuVerify & Screener).\n\n"
            "Best regards,\nRecruitment Team\nAcme Tech Solutions"
        ),
        sender_email="agentic.hr.test.ubaid.recruiter@gmail.com",
    )

    result = await email_service.send_email(message)
    activity.logger.info(
        f"Email notification result: {result.get('status')} (fake={result.get('fake')})"
    )
    return result


@activity.defn
async def schedule_interview_activity(candidate_name: str, candidate_email: str) -> dict[str, Any]:
    """
    Schedules a technical assessment / screening interview.
    Routes strictly through FakeCalendarService (simulated Google Meet event).
    """
    _require_fake_integrations("schedule_interview_activity")
    activity.logger.info(f"Executing schedule_interview_activity for {candidate_name}")
    calendar_service = get_calendar_service()

    start_time = datetime.now(UTC) + timedelta(days=2)
    end_time = start_time + timedelta(minutes=45)

    event = CalendarEvent(
        title=f"Technical Assessment Interview - {candidate_name}",
        start_time=start_time,
        end_time=end_time,
        attendees=[
            candidate_email,
            "agentic.hr.test.ubaid.hm@gmail.com",
            "agentic.hr.test.ubaid.recruiter@gmail.com",
        ],
        description="Autonomous initial candidate screening and rubric evaluation discussion.",
        location="Google Meet",
    )

    result = await calendar_service.create_event(event)
    activity.logger.info(
        f"Calendar event result: {result.get('status')} (meet={result.get('details', {}).get('hangout_link')})"
    )
    return result


@activity.defn
async def notify_changes_requested_activity(
    approval_id: str,
    requester_email: str,
    instructions: str | None,
) -> dict[str, Any]:
    """
    Sends simulated email notification informing the requester that corrections are required.
    Routes strictly through FakeGmailService.
    """
    _require_fake_integrations("notify_changes_requested_activity")
    activity.logger.info(f"Executing notify_changes_requested_activity for approval {approval_id}")
    email_service = get_email_service()

    message = EmailMessage(
        to_email=requester_email,
        subject=f"Corrections Required: Approval Request #{approval_id[:8]}",
        body_text=(
            f"Dear Requester,\n\n"
            f"Your approval request #{approval_id} has been returned for corrections.\n\n"
            f"Reviewer Instructions:\n{instructions or 'No specific instructions provided.'}\n\n"
            f"Please visit the Agentic HR correction page to update and resubmit your draft.\n\n"
            f"Best regards,\nAgentic HR Workforce Governance"
        ),
        sender_email="agentic.hr.test.ubaid.recruiter@gmail.com",
    )
    return await email_service.send_email(message)


@activity.defn
async def notify_approval_decision_activity(
    approval_id: str,
    decision: str,
    reason: str | None,
) -> dict[str, Any]:
    """
    Dispatches terminal decision notification (Approved or Rejected) to relevant parties.
    Routes strictly through FakeGmailService.
    """
    _require_fake_integrations("notify_approval_decision_activity")
    activity.logger.info(
        f"Executing notify_approval_decision_activity for approval {approval_id}: {decision}"
    )
    email_service = get_email_service()

    message = EmailMessage(
        to_email="agentic.hr.test.ubaid.employee@gmail.com",
        subject=f"Approval Request #{approval_id[:8]} - Decision: {decision.upper()}",
        body_text=(
            f"Your approval request #{approval_id} has reached a terminal decision: {decision.upper()}.\n\n"
            f"Details / Remarks: {reason or 'None'}\n\n"
            f"Best regards,\nAgentic HR Workforce Governance"
        ),
        sender_email="agentic.hr.test.ubaid.owner@gmail.com",
    )
    return await email_service.send_email(message)


@activity.defn
async def notify_resubmitted_activity(
    approval_id: str,
    revision: int,
    approver_role: str,
) -> dict[str, Any]:
    """
    Notifies the assigned approver role that a corrected draft has been resubmitted (Round N).
    Routes strictly through FakeGmailService.
    """
    _require_fake_integrations("notify_resubmitted_activity")
    activity.logger.info(
        f"Executing notify_resubmitted_activity for approval {approval_id} (rev {revision}, role {approver_role})"
    )
    email_service = get_email_service()

    message = EmailMessage(
        to_email=f"agentic.hr.test.ubaid.{approver_role}@gmail.com",
        subject=f"Corrected Approval Request #{approval_id[:8]} Ready for Review (Round {revision})",
        body_text=(
            f"Approval request #{approval_id} has been updated and resubmitted by the requester.\n\n"
            f"Revision Round: {revision}\n"
            f"Assigned Approver Role: {approver_role}\n\n"
            f"Please review the updated submission in your Approvals Inbox.\n\n"
            f"Best regards,\nAgentic HR Automated Dispatcher"
        ),
        sender_email="agentic.hr.test.ubaid.recruiter@gmail.com",
    )
    return await email_service.send_email(message)


# ==============================================================================
# Phase 7: Verification & Dummy Approval Activities
# ==============================================================================


@activity.defn
async def verify_approval_record_activity(
    approval_id: str,
    expected_decision: str,
    decided_by: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Validates a human approval decision against the authoritative PostgreSQL database.
    Enforces ADR 002, ADR 003, and Stage 2 Phase 7:
    1. Connects to PostgreSQL via AsyncSessionLocal.
    2. If tenant_id is provided, sets transaction-local app.tenant_id for RLS validation.
    3. Fetches the Approval record by ID.
    4. Validates that the record exists and is accessible.
    5. Validates that record.status matches expected_decision (e.g. 'approved' or 'rejected').
    6. Validates that str(record.decided_by) matches decided_by.
    7. Raises ApplicationError(..., non_retryable=True) if verification fails.
    """
    activity.logger.info(
        f"Verifying approval {approval_id} in database (expected: {expected_decision}, decided_by: {decided_by})"
    )
    try:
        approval_uuid = UUID(approval_id)
    except ValueError as exc:
        raise ApplicationError(
            f"Invalid approval_id UUID: {approval_id}",
            non_retryable=True,
        ) from exc

    expected_tenant_uuid: UUID | None = None
    if tenant_id:
        try:
            expected_tenant_uuid = UUID(str(tenant_id).strip())
        except ValueError as exc:
            raise ApplicationError(
                f"Invalid tenant_id UUID for approval verification: {tenant_id}",
                non_retryable=True,
            ) from exc

    async with AsyncSessionLocal() as session:
        if expected_tenant_uuid is not None:
            await set_tenant_context(session, str(expected_tenant_uuid))

        stmt = select(Approval).where(Approval.id == approval_uuid)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            raise ApplicationError(
                f"Approval record '{approval_id}' not found in database or inaccessible under tenant context",
                non_retryable=True,
            )

        if expected_tenant_uuid is not None and record.tenant_id != expected_tenant_uuid:
            raise ApplicationError(
                f"Tenant mismatch for approval '{approval_id}': record tenant is '{record.tenant_id}', expected '{expected_tenant_uuid}'",
                non_retryable=True,
            )

        if record.status.lower() != expected_decision.lower():
            raise ApplicationError(
                f"Database status mismatch for approval '{approval_id}': record status is '{record.status}', expected '{expected_decision}'",
                non_retryable=True,
            )

        if record.decided_by is None or str(record.decided_by) != decided_by:
            raise ApplicationError(
                f"Database decided_by mismatch for approval '{approval_id}': record decided_by is '{record.decided_by}', expected '{decided_by}'",
                non_retryable=True,
            )

        activity.logger.info(
            f"Successfully verified approval {approval_id} in database: status={record.status}, decided_by={record.decided_by}"
        )
        return {
            "verified": True,
            "approval_id": str(record.id),
            "status": record.status,
            "decided_by": str(record.decided_by),
            "decision_reason": record.decision_reason,
            "current_revision": record.current_revision,
            "tenant_id": str(record.tenant_id),
        }


@activity.defn
async def get_approval_decision_activity(
    approval_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Polls the authoritative PostgreSQL database to reconcile an approval decision
    if a Temporal signal was dropped due to a transient network or server outage.
    Returns decision metadata if the approval is already in a terminal/decided state
    ('approved' or 'rejected'), or None if still pending.
    """
    try:
        approval_uuid = UUID(approval_id)
    except ValueError:
        return None

    async with AsyncSessionLocal() as session:
        if tenant_id:
            await set_tenant_context(session, str(UUID(str(tenant_id).strip())))

        stmt = select(Approval).where(Approval.id == approval_uuid)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None

        status_lower = record.status.lower()
        if status_lower in ("approved", "rejected") and record.decided_by is not None:
            return {
                "approval_id": str(record.id),
                "action": status_lower,
                "decided_by": str(record.decided_by),
                "tenant_id": str(record.tenant_id),
                "reason": record.decision_reason,
            }
        return None


@activity.defn
async def execute_dummy_approved_action_activity(approval_id: str) -> dict[str, Any]:
    """
    Executes downstream business action for an approved request.
    Simulates employee onboarding or payroll dispatch without external side effects.
    """
    activity.logger.info(f"Executing approved business action for approval {approval_id}")
    return {
        "status": "SUCCESS",
        "action": "APPROVED_ACTION_EXECUTED",
        "approval_id": approval_id,
        "executed_at": datetime.now(UTC).isoformat(),
    }


@activity.defn
async def execute_dummy_rejected_action_activity(approval_id: str) -> dict[str, Any]:
    """
    Executes downstream rejection cleanup for a rejected request.
    Simulates notification and state cleanup without external side effects.
    """
    activity.logger.info(f"Executing rejected cleanup action for approval {approval_id}")
    return {
        "status": "SUCCESS",
        "action": "REJECTED_CLEANUP_EXECUTED",
        "approval_id": approval_id,
        "executed_at": datetime.now(UTC).isoformat(),
    }


# ==============================================================================
# Stage 4 Prompt 5: Tenant-Scoped Email Dispatch & Document Expiry Activities
# (Closes Pass 2 B2 handoff for Stage 4+ workflows)
# ==============================================================================


@dataclass
class TenantEmailRequest:
    """Tenant-scoped outbound email payload routed through EmailDeliveryService."""

    tenant_id: str
    to_email: str
    template_name: str
    template_variables: dict[str, Any]
    idempotency_key: str
    actor_id: str | None = None


@dataclass
class TenantExpiryScanRequest:
    """Tenant-scoped document expiry scan parameters."""

    tenant_id: str
    reference_date_iso: str | None = None
    send_emails: bool = True


@activity.defn
async def send_tenant_email_activity(req: TenantEmailRequest) -> dict[str, Any]:
    """
    Sends a tenant-scoped outbound email via `EmailDeliveryService.send_email()`
    (enforcing `execute_connector_call`, RLS, read-only mode, rate limits, and
    IdempotencyGate for onboarding invitations and expiry alerts).
    """
    try:
        t_id = UUID(str(req.tenant_id).strip())
    except ValueError as exc:
        raise ApplicationError(
            f"Invalid tenant_id UUID for send_tenant_email_activity: {req.tenant_id}",
            non_retryable=True,
        ) from exc

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(t_id))
        try:
            result = await EmailDeliveryService.send_email(
                session=session,
                tenant_id=t_id,
                to_email=req.to_email,
                template_name=req.template_name,
                template_variables=req.template_variables,
                idempotency_key=req.idempotency_key,
                actor_id=req.actor_id,
            )
            await session.commit()
            return result
        except ConnectorAuthExpiredError as exc:
            await session.commit()
            return {"status": "skipped", "reason": "auth_expired", "detail": str(exc)}
        except GmailDeliveryError as exc:
            await session.commit()
            return {"status": "skipped", "reason": "delivery_error", "detail": str(exc)}
        except (ReadOnlyConnectorError, CredentialNotFoundError, RecipientBlockedError) as exc:
            await session.rollback()
            return {"status": "skipped", "reason": type(exc).__name__, "detail": str(exc)}


@activity.defn
async def list_active_tenant_ids_activity(limit: int = 500) -> list[str]:
    """
    Discovers all active, non-suspended tenant UUIDs using the narrow SECURITY DEFINER
    function `public.document_scan_tenant_ids(integer)`.
    """
    async with AsyncSessionLocal() as session:
        tenant_ids = await DocumentExpiryService.list_active_tenant_ids(session, limit=limit)
        return [str(tid) for tid in tenant_ids]


@activity.defn
async def scan_tenant_expiring_documents_activity(
    req: TenantExpiryScanRequest,
) -> dict[str, Any]:
    """
    Scans a single tenant for documents expiring in 30, 7, or 1 days (PKT),
    persists deduped in-app notifications, and dispatches idempotent Gmail alerts.
    """
    try:
        t_id = UUID(str(req.tenant_id).strip())
    except ValueError as exc:
        raise ApplicationError(
            f"Invalid tenant_id UUID for scan_tenant_expiring_documents_activity: {req.tenant_id}",
            non_retryable=True,
        ) from exc

    ref_date: date | None = None
    if req.reference_date_iso:
        try:
            ref_date = date.fromisoformat(req.reference_date_iso)
        except ValueError as exc:
            raise ApplicationError(
                f"Invalid reference_date_iso '{req.reference_date_iso}'",
                non_retryable=True,
            ) from exc

    async with AsyncSessionLocal() as session:
        summary = await DocumentExpiryService.scan_expiring_documents_for_tenant(
            session=session,
            tenant_id=t_id,
            reference_date_pkt=ref_date,
            send_emails=req.send_emails,
        )
        return summary.model_dump(mode="json")
