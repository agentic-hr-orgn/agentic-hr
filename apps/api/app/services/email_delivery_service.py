"""
Email Delivery Orchestration Service for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and ADR 006 (Google Workspace Integration).

Features:
- Enforces client-side Read-Only switch (ReadOnlyConnectorError / HTTP 403).
- Enforces Redis sliding-window per-tenant rate limit.
- Enforces Stage 2 IdempotencyGate for at-most-once delivery.
- Automatically handles 401 / invalid_grant by triggering needs_reconnect & job pause.
- Transactional persistence in public.sent_messages with fail-closed RLS.
- Synchronous bounce handling & marking without reading inboxes.
- Immutable audit logging.
"""

import logging
import uuid
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import IdempotencyGate, compute_payload_hash
from app.integrations.base import EmailMessage, GmailDeliveryError
from app.integrations.factory import get_email_service
from app.models.connector import ConnectorCredential, SentMessage
from app.services.audit_service import write_audit
from app.services.connector_call_wrapper import execute_connector_call
from app.services.connector_service import (
    ConnectorService,
)
from app.services.template_service import render_email

logger = logging.getLogger("agentic_hr.email_delivery")


class RecipientBlockedError(Exception):
    """Raised when an outbound email is blocked because the recipient has a prior bounce or failure."""


class EmailDeliveryService:
    """Orchestrates outbound emails with idempotency, rate limiting, and audit logging."""

    @staticmethod
    async def is_recipient_failed(
        session: AsyncSession,
        tenant_id: UUID | str,
        recipient_email: str,
    ) -> bool:
        """
        Returns True if the recipient has previously experienced a bounced or failed email delivery.
        Used to prevent repeated automated sends to invalid or failing addresses (Strict ADR 006).
        """
        t_id = UUID(str(tenant_id))
        stmt = (
            select(SentMessage.id)
            .where(
                SentMessage.tenant_id == t_id,
                func.lower(SentMessage.recipient_email) == recipient_email.strip().lower(),
                SentMessage.status.in_(["bounced", "failed"]),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalars().first() is not None

    @staticmethod
    async def send_email(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        to_email: str,
        subject: str | None = None,
        body_text: str | None = None,
        template_name: str | None = None,
        template_variables: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
        force_retry: bool = False,
    ) -> dict[str, Any]:
        t_id = UUID(str(tenant_id))
        clean_email = to_email.strip().lower()

        # 1. Hard-Bounce / Failure Circuit-Breaker Check
        if not force_retry and await EmailDeliveryService.is_recipient_failed(
            session, t_id, clean_email
        ):
            raise RecipientBlockedError(
                f"Recipient '{clean_email}' has previously bounced or failed delivery. Set force_retry=True to attempt delivery."
            )

        # 2. Retrieve Credential
        if credential_id:
            cred = await ConnectorService.get_credential_by_id(session, t_id, credential_id)
        else:
            cred = await ConnectorService.get_active_credential(session, t_id, provider="google")

        # 3. Render Email Template or Use Provided Text
        if template_name:
            rendered_subj, rendered_html, rendered_text = render_email(
                template_name, template_variables or {}
            )
            subj = rendered_subj
            html_body = rendered_html
            text_body = rendered_text
        else:
            subj = subject or "Notification"
            text_body = body_text or ""
            html_body = None

        payload_to_hash = {
            "to": clean_email,
            "subject": subj,
            "text": text_body,
            "html": html_body,
            "credential_id": str(cred.id),
        }
        payload_hash = compute_payload_hash(payload_to_hash)

        # 4. Enforce Database Idempotency for At-Most-Once Delivery
        effective_key = idempotency_key or f"auto_{uuid.uuid4()}"
        async with IdempotencyGate(
            session=session,
            tenant_id=t_id,
            operation="email.send",
            idempotency_key=effective_key,
            payload_hash=payload_hash,
        ) as gate:
            if gate.is_replay:
                return gate.response_body

            res = await EmailDeliveryService._dispatch_and_record(
                session=session,
                tenant_id=t_id,
                cred=cred,
                to_email=clean_email,
                subject=subj,
                html_body=html_body,
                text_body=text_body,
                template_name=template_name,
                idempotency_key=effective_key,
                actor_id=actor_id,
            )
            gate.set_result(200, res)
            return res

    @staticmethod
    async def _dispatch_and_record(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        cred: ConnectorCredential,
        to_email: str,
        subject: str,
        html_body: str | None,
        text_body: str,
        template_name: str | None,
        idempotency_key: str | None,
        actor_id: UUID | str | None,
    ) -> dict[str, Any]:
        email_svc = get_email_service()
        msg = EmailMessage(
            to_email=to_email,
            subject=subject,
            body_text=text_body,
            body_html=html_body,
            sender_email=cred.account_email,
        )

        sent_msg_id = uuid.uuid4()

        # Enforce Read-Only, Rate Limits, Expiry/Refresh, Retries, and Audit via execute_connector_call
        try:
            send_result = await execute_connector_call(
                session=session,
                tenant_id=tenant_id,
                credential=cred,
                operation="email.send",
                is_write=True,
                call_factory=lambda token: email_svc.send_email(msg, access_token=token),
                actor_id=actor_id,
                rate_limit_scope="gmail.send",
                max_requests_per_minute=60,
                audit_target_type="sent_message",
                audit_target_id=sent_msg_id,
                audit_details={
                    "recipient": to_email,
                    "subject": subject,
                    "template_name": template_name,
                },
            )
        except GmailDeliveryError as exc:
            err_msg = str(exc)
            sent_record = SentMessage(
                id=sent_msg_id,
                tenant_id=tenant_id,
                credential_id=cred.id,
                idempotency_key=idempotency_key,
                recipient_email=to_email,
                subject=subject,
                template_name=template_name,
                html_body=html_body,
                text_body=text_body,
                provider_message_id=None,
                status="failed",
                error_message=err_msg,
            )
            session.add(sent_record)
            await session.flush()

            await write_audit(
                session,
                action="email.failed",
                target_type="sent_message",
                target_id=sent_msg_id,
                details={
                    "recipient": to_email,
                    "subject": subject,
                    "error": err_msg,
                    "status": "failed",
                },
                actor_id=actor_id,
                tenant_id=tenant_id,
            )
            raise

        provider_msg_id = send_result.get("message_id")

        # Record in sent_messages
        sent_record = SentMessage(
            id=sent_msg_id,
            tenant_id=tenant_id,
            credential_id=cred.id,
            idempotency_key=idempotency_key,
            recipient_email=to_email,
            subject=subject,
            template_name=template_name,
            html_body=html_body,
            text_body=text_body,
            provider_message_id=provider_msg_id,
            status="sent",
            error_message=None,
        )
        session.add(sent_record)
        await session.flush()

        return {
            "status": "success",
            "message_id": provider_msg_id,
            "sent_message_id": str(sent_record.id),
            "recipient": to_email,
            "fake": send_result.get("fake", False),
        }

    @staticmethod
    async def record_bounce(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        provider_message_id: str,
        error_message: str,
        actor_id: UUID | str | None = None,
    ) -> SentMessage:
        """
        Synchronously marks a message delivery as bounced/failed without inbox reading.
        Updates sent_messages status to 'bounced' and records an immutable audit log.
        """
        t_id = UUID(str(tenant_id))
        clean_id = provider_message_id.strip()

        msg_uuid = None
        try:
            msg_uuid = UUID(clean_id)
        except (ValueError, AttributeError):
            pass

        if msg_uuid:
            condition = (SentMessage.provider_message_id == clean_id) | (SentMessage.id == msg_uuid)
        else:
            condition = SentMessage.provider_message_id == clean_id

        stmt = (
            select(SentMessage)
            .where(
                SentMessage.tenant_id == t_id,
                condition,
            )
            .order_by(desc(SentMessage.created_at))
        )
        result = await session.execute(stmt)
        record = result.scalars().first()

        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Sent message with provider ID '{provider_message_id}' not found.",
            )

        record.status = "bounced"
        record.error_message = error_message
        await session.flush()

        await write_audit(
            session,
            action="email.bounced",
            target_type="sent_message",
            target_id=record.id,
            details={
                "provider_message_id": provider_message_id,
                "recipient_email": record.recipient_email,
                "reason": error_message,
            },
            actor_id=actor_id,
            tenant_id=t_id,
        )
        return record
