"""
Transactional Outbox Service for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and Stage 2 Phase 8 specifications.

Guarantees Dual-Write elimination by queueing external events within the
exact same PostgreSQL transaction as the business entity mutation.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import OutboxEvent
from app.models.tenant import set_tenant_context

logger = logging.getLogger("agentic_hr.outbox")


async def enqueue_outbox_event(
    session: AsyncSession,
    tenant_id: UUID | str,
    event_type: str,
    event_payload: dict[str, Any],
    next_attempt_at: datetime | None = None,
) -> OutboxEvent:
    """
    Appends an event to the transactional outbox within an open database transaction.
    DOES NOT commit the transaction - the caller must commit so that the business
    entity mutation and the outbox event commit atomically.
    """
    clean_tenant_id = UUID(str(tenant_id))
    scheduled_at = next_attempt_at or datetime.now(UTC)

    event = OutboxEvent(
        tenant_id=clean_tenant_id,
        event_type=event_type,
        event_payload=event_payload,
        status="pending",
        attempts=0,
        next_attempt_at=scheduled_at,
    )
    session.add(event)
    await session.flush()  # Populates event.id without committing transaction

    logger.debug(f"Enqueued outbox event: id={event.id} type={event_type} tenant={clean_tenant_id}")
    return event


async def get_outbox_event(
    session: AsyncSession,
    event_id: UUID | str,
    tenant_id: UUID | str | None = None,
) -> OutboxEvent | None:
    """Retrieves a single outbox event by its ID, ensuring tenant context is set for RLS."""
    if tenant_id:
        await set_tenant_context(session, str(tenant_id))
    clean_id = UUID(str(event_id))
    stmt = select(OutboxEvent).where(OutboxEvent.id == clean_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
