"""
Document Expiry Scanner Service for Agentic HR Core (Stage 4 Prompt 5).

Scans `public.pub_documents_expiring` for active/on-leave employee documents expiring in
exactly 30, 7, or 1 days relative to today in `Asia/Karachi` (PKT, UTC+05:00).

Two-phase governance pattern (Pass 2 invariant):
- Phase 1: Insert deduped in-app notifications (`ON CONFLICT DO NOTHING`) + write audit log,
  then COMMIT and immediately re-establish `set_tenant_context`.
- Phase 2: Dispatch idempotent Gmail alerts via `EmailDeliveryService.send_email()` using
  `document_expiry_alert` template and deterministic `idempotency_key`. Read-only, disconnected,
  or expired connectors never crash the scan or roll back committed in-app notifications.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezones import get_karachi_tz
from app.integrations.base import GmailDeliveryError
from app.models.connector import SentMessage
from app.models.employee import Employee
from app.models.membership import Membership
from app.models.role import Role
from app.models.tenant import set_tenant_context
from app.models.tenant_entity import Tenant
from app.models.user import User
from app.schemas.notification import ExpiryScanSummary
from app.services.audit_service import write_audit
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    CredentialNotFoundError,
    ReadOnlyConnectorError,
)
from app.services.email_delivery_service import EmailDeliveryService, RecipientBlockedError
from app.services.notification_service import NotificationService

logger = logging.getLogger("agentic_hr.services.document_expiry")

ALERT_WINDOWS_DAYS: tuple[int, ...] = (30, 7, 1)
SEVERITY_BY_DAYS: dict[int, str] = {
    30: "info",
    7: "warning",
    1: "critical",
}
ACTIVE_EMPLOYEE_STATUSES: tuple[str, ...] = ("active", "on_leave")


class DocumentExpiryService:
    """Scans tenant employee documents for 30/7/1-day expiry windows and dispatches alerts."""

    @staticmethod
    async def list_active_tenant_ids(
        session: AsyncSession,
        *,
        limit: int = 500,
    ) -> list[UUID]:
        """
        Discovers active, non-suspended tenant UUIDs via the narrow SECURITY DEFINER
        function `public.document_scan_tenant_ids(integer)`.
        """
        result = await session.execute(
            text("SELECT public.document_scan_tenant_ids(:limit)"),
            {"limit": limit},
        )
        return [UUID(str(row[0])) for row in result.all() if row[0] is not None]

    @staticmethod
    async def _resolve_admin_recipients(
        session: AsyncSession,
        tenant_id: UUID,
    ) -> list[tuple[UUID, str]]:
        """Returns active Owner and HR Head (user_id, email) tuples for the tenant."""
        stmt = (
            select(User.id, User.email)
            .join(Membership, Membership.user_id == User.id)
            .join(Role, Role.id == Membership.role_id)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.status == "active",
                (Role.role_key == "owner") | (Membership.is_hr_head.is_(True)),
            )
            .order_by(User.id)
        )
        rows = (await session.execute(stmt)).all()
        seen: set[UUID] = set()
        recipients: list[tuple[UUID, str]] = []
        for user_id, email in rows:
            if user_id not in seen and email:
                seen.add(user_id)
                recipients.append((user_id, email.strip().lower()))
        return recipients

    @staticmethod
    async def _resolve_linked_employee_users(
        session: AsyncSession,
        tenant_id: UUID,
        employee_ids: list[UUID],
    ) -> dict[UUID, tuple[UUID, str]]:
        """
        Maps `employee_id -> (user_id, user_email)` ONLY for employees linked to a real User
        account (`employees.user_id IS NOT NULL`), avoiding bounces to unlinked `.internal` emails.
        """
        if not employee_ids:
            return {}
        stmt = (
            select(Employee.id, User.id, User.email)
            .join(User, User.id == Employee.user_id)
            .where(
                Employee.tenant_id == tenant_id,
                Employee.id.in_(employee_ids),
                Employee.user_id.is_not(None),
            )
        )
        rows = (await session.execute(stmt)).all()
        return {
            emp_id: (user_id, email.strip().lower())
            for emp_id, user_id, email in rows
            if user_id is not None and email
        }

    @classmethod
    async def scan_expiring_documents_for_tenant(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        reference_date_pkt: date | None = None,
        actor_id: UUID | str | None = None,
        clerk_user_id: str | None = None,
        send_emails: bool = True,
    ) -> ExpiryScanSummary:
        """
        Scans `public.pub_documents_expiring` for 30, 7, and 1-day expiry windows relative to
        `reference_date_pkt` (defaults to today in `Asia/Karachi`), persists deduped in-app
        notifications in Phase 1, commits, and dispatches idempotent Gmail alerts in Phase 2.
        """
        t_id = UUID(str(tenant_id))
        ref_date = reference_date_pkt or datetime.now(get_karachi_tz()).date()
        target_dates = [ref_date + timedelta(days=d) for d in ALERT_WINDOWS_DAYS]

        await set_tenant_context(session, str(t_id), clerk_user_id)

        tenant_name = (
            await session.execute(select(Tenant.name).where(Tenant.id == t_id))
        ).scalar_one_or_none() or "Agentic HR"

        # Query strictly from hardened `public.pub_documents_expiring` (security_invoker=true)
        view_sql = text(
            """
            SELECT
                document_id,
                employee_id,
                employee_name,
                employee_email,
                employee_status,
                document_type,
                title,
                expiry_date
            FROM public.pub_documents_expiring
            WHERE tenant_id = :tenant_id
              AND expiry_date = ANY(:target_dates)
              AND employee_status = ANY(:active_statuses)
            ORDER BY expiry_date ASC, document_id ASC
            """
        )
        doc_rows = (
            (
                await session.execute(
                    view_sql,
                    {
                        "tenant_id": t_id,
                        "target_dates": target_dates,
                        "active_statuses": list(ACTIVE_EMPLOYEE_STATUSES),
                    },
                )
            )
            .mappings()
            .all()
        )

        admin_recipients = await cls._resolve_admin_recipients(session, t_id)
        employee_ids = list({row["employee_id"] for row in doc_rows})
        linked_users = await cls._resolve_linked_employee_users(session, t_id, employee_ids)

        alerts_created = 0
        alerts_deduplicated = 0
        email_dispatches: list[dict[str, Any]] = []

        # ------------------------------------------------------------------
        # Phase 1: Insert Deduped In-App Notifications + Audit Log -> COMMIT
        # ------------------------------------------------------------------
        for row in doc_rows:
            doc_id: UUID = row["document_id"]
            emp_id: UUID = row["employee_id"]
            emp_name: str = row["employee_name"]
            doc_title: str = row["title"]
            doc_type: str = row["document_type"]
            exp_date: date = row["expiry_date"]
            days_remaining = (exp_date - ref_date).days

            if days_remaining not in SEVERITY_BY_DAYS:
                continue

            severity = SEVERITY_BY_DAYS[days_remaining]
            base_key = f"doc-expiry:{doc_id}:{days_remaining}:{exp_date.isoformat()}"
            day_label = "day" if days_remaining == 1 else "days"
            notif_title = f"{doc_title} expires in {days_remaining} {day_label}"
            notif_message = (
                f"{emp_name}'s {doc_type.upper()} document '{doc_title}' expires on "
                f"{exp_date.isoformat()} ({days_remaining} {day_label} remaining)."
            )

            # Build deduplicated recipient list for this document: Owners + HR Heads + linked employee
            doc_recipients: dict[UUID, str] = dict(admin_recipients)
            linked = linked_users.get(emp_id)
            if linked is not None:
                linked_uid, linked_email = linked
                doc_recipients.setdefault(linked_uid, linked_email)

            emailed_addresses_for_doc: set[str] = set()
            for recipient_uid, recipient_email in doc_recipients.items():
                notif_dedupe_key = f"{base_key}:u:{recipient_uid}"
                created_id = await NotificationService.create_deduped(
                    session,
                    tenant_id=t_id,
                    recipient_user_id=recipient_uid,
                    recipient_email=recipient_email,
                    category="document_expiry",
                    severity=severity,
                    title=notif_title,
                    message=notif_message,
                    entity_type="employee_document",
                    entity_id=doc_id,
                    dedupe_key=notif_dedupe_key,
                )
                if created_id is not None:
                    alerts_created += 1
                else:
                    alerts_deduplicated += 1

                if recipient_email and recipient_email not in emailed_addresses_for_doc:
                    emailed_addresses_for_doc.add(recipient_email)
                    email_dispatches.append(
                        {
                            "to_email": recipient_email,
                            "idempotency_key": f"{base_key}:e:{recipient_email}",
                            "template_variables": {
                                "employee_name": emp_name,
                                "document_name": doc_title,
                                "expiry_date": exp_date.isoformat(),
                                "days_until_expiry": str(days_remaining),
                                "action_url": f"http://localhost:3000/employees/{emp_id}",
                                "company_name": tenant_name,
                            },
                        }
                    )

        await write_audit(
            session,
            action="document_expiry.scan_completed",
            target_type="tenant",
            target_id=t_id,
            details={
                "reference_date": ref_date.isoformat(),
                "documents_matched": len(doc_rows),
                "alerts_created": alerts_created,
                "alerts_deduplicated": alerts_deduplicated,
            },
            actor_id=actor_id,
            tenant_id=t_id,
        )

        # Commit all domain writes BEFORE any external Gmail call!
        await session.commit()
        await set_tenant_context(session, str(t_id), clerk_user_id)

        # ------------------------------------------------------------------
        # Phase 2: Post-Commit Idempotent Gmail Dispatch
        # ------------------------------------------------------------------
        emails_sent = 0
        emails_skipped = 0

        if send_emails:
            for item in email_dispatches:
                idem_key = item["idempotency_key"]
                # Check if already sent under this idempotency key so replay isn't double-counted
                already_sent = (
                    await session.execute(
                        select(SentMessage.id)
                        .where(
                            SentMessage.tenant_id == t_id,
                            SentMessage.idempotency_key == idem_key,
                            SentMessage.status == "sent",
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()

                try:
                    await EmailDeliveryService.send_email(
                        session=session,
                        tenant_id=t_id,
                        to_email=item["to_email"],
                        template_name="document_expiry_alert",
                        template_variables=item["template_variables"],
                        idempotency_key=idem_key,
                        actor_id=actor_id,
                    )
                    await session.commit()
                    await set_tenant_context(session, str(t_id), clerk_user_id)
                    if already_sent is None:
                        emails_sent += 1
                except ConnectorAuthExpiredError:
                    logger.warning(
                        "Google connector 401 during expiry alert for tenant %s; "
                        "persisting circuit-breaker trip while preserving in-app notifications.",
                        t_id,
                    )
                    await session.commit()
                    await set_tenant_context(session, str(t_id), clerk_user_id)
                    emails_skipped += 1
                except GmailDeliveryError:
                    logger.warning(
                        "Gmail delivery error during expiry alert for tenant %s (%s).",
                        t_id,
                        item["to_email"],
                    )
                    await session.commit()
                    await set_tenant_context(session, str(t_id), clerk_user_id)
                    emails_skipped += 1
                except (
                    ReadOnlyConnectorError,
                    CredentialNotFoundError,
                    RecipientBlockedError,
                ) as exc:
                    logger.warning(
                        "Skipping expiry alert email for tenant %s (%s): %s",
                        t_id,
                        item["to_email"],
                        exc,
                    )
                    await session.rollback()
                    await set_tenant_context(session, str(t_id), clerk_user_id)
                    emails_skipped += 1
                except Exception:
                    logger.exception(
                        "Unexpected error sending expiry alert email for tenant %s",
                        t_id,
                    )
                    await session.rollback()
                    await set_tenant_context(session, str(t_id), clerk_user_id)
                    emails_skipped += 1

        return ExpiryScanSummary(
            tenant_id=t_id,
            reference_date=ref_date.isoformat(),
            documents_matched=len(doc_rows),
            alerts_created=alerts_created,
            alerts_deduplicated=alerts_deduplicated,
            emails_sent=emails_sent,
            emails_skipped=emails_skipped,
        )
