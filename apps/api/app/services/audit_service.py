"""
Audit Logging Service for Agentic HR Core.
Complies with ADR 002 (Tenancy), ADR 003 (Privacy Boundary), and Stage 2 Phase 5 specifications.

Provides:
1. write_audit: Enqueues an immutable audit entry in the same transaction as business operations.
2. sanitize_value: Recursively redacts credentials, secrets, and Pakistani CNICs.
3. get_tenant_audit_logs: Queries audit logs scoped to the active tenant.
"""

import logging
import re
import uuid
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.middleware import client_ip_ctx, request_id_ctx, user_agent_ctx
from app.models.audit_log import AuditLog

logger = logging.getLogger("agentic_hr.audit")

# Pakistani CNIC Patterns: 12345-1234567-1 (formatted) or 1234512345671 (unformatted 13 digits)
CNIC_REGEX = re.compile(r"\b\d{5}-\d{7}-\d{1}\b|\b\d{13}\b")

# Sensitive Dictionary Keys to Redact
SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "pass",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "authorization",
    "auth",
    "private_key",
    "credit_card",
    "cvv",
    "card_number",
    "cnic",
    "cnic_number",
    "national_id",
    "ssn",
    "tax_id",
}


def sanitize_value(val: Any) -> Any:
    """
    Recursively redacts sensitive credentials and Pakistani CNIC numbers.
    Never mutates inputs in place; returns a sanitized deep-copy.
    """
    if isinstance(val, dict):
        sanitized_dict: dict[str, Any] = {}
        for k, v in val.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ["cnic", "national_id"]):
                sanitized_dict[k] = "[REDACTED_CNIC]"
            elif any(s in k_lower for s in SENSITIVE_KEYS):
                sanitized_dict[k] = "[REDACTED_SECRET]"
            else:
                sanitized_dict[k] = sanitize_value(v)
        return sanitized_dict
    elif isinstance(val, list):
        return [sanitize_value(item) for item in val]
    elif isinstance(val, str):
        # 1. Redact Pakistani CNICs matching pattern
        cleaned = CNIC_REGEX.sub("[REDACTED_CNIC]", val)
        # 2. Redact Bearer authorization tokens in string bodies
        cleaned = re.sub(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "Bearer [REDACTED_TOKEN]", cleaned)
        return cleaned
    return val


async def write_audit(
    session: AsyncSession,
    *,
    action: str,
    target_type: str,
    target_id: str | uuid.UUID | None,
    details: dict[str, Any] | None = None,
    actor_id: str | uuid.UUID | None = None,
    tenant_id: str | uuid.UUID | None = None,
    request_id: str | None = None,
) -> AuditLog:
    """
    Enqueues an immutable AuditLog entry within the caller's active database transaction.
    Guarantees atomicity: commits with the business operation or rolls back on failure.

    Enrichment:
    - Automatically attaches sanitized client IP and user-agent from middleware contextvars.
    - Automatically resolves request_id from middleware contextvar if omitted.
    """
    # 1. Sanitize payload
    clean_details = sanitize_value(details or {})

    # 2. Attach safe request metadata
    req_meta = {
        "client_ip": client_ip_ctx.get(),
        "user_agent": user_agent_ctx.get(),
    }
    clean_details["_request"] = {k: v for k, v in req_meta.items() if v is not None}

    # 3. Resolve tenant_id
    if tenant_id is None:
        raise ValueError("tenant_id is required to write an audit log entry.")

    resolved_tenant_uuid = (
        tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    )
    resolved_actor_uuid = (
        actor_id
        if (actor_id is None or isinstance(actor_id, uuid.UUID))
        else uuid.UUID(str(actor_id))
    )

    audit_entry = AuditLog(
        tenant_id=resolved_tenant_uuid,
        actor_id=resolved_actor_uuid,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id else None,
        details=clean_details,
        request_id=request_id or request_id_ctx.get(),
    )

    session.add(audit_entry)
    logger.debug(
        f"[Audit Enqueued] {action} on {target_type}:{target_id} by actor {actor_id} in tenant {tenant_id}"
    )
    return audit_entry


async def get_tenant_audit_logs(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditLog]:
    """
    Retrieves audit logs for the current tenant context, ordered newest first.
    Subject to database Row-Level Security (RLS).
    """
    stmt = select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())
