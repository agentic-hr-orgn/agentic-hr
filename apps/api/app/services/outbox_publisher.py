"""
Transactional Outbox Publisher Worker for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy), ADR 006 (Fake Integrations), and Stage 2 Phase 8 specifications.

Features:
- Safe concurrency claiming via SELECT ... FOR UPDATE SKIP LOCKED
- At-least-once delivery with downstream deduplication using immutable event UUIDs
- Exponential backoff retry for transient delivery failures
- Dead-letter handling for permanent failures
- Stale lock reclamation for worker-restart resilience
- Multi-tenant event dispatching with strict fake adapters (USE_FAKE_INTEGRATIONS=true)
- Robust transaction-local RLS tenant context maintenance across claim & publish commits
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.base import EmailMessage
from app.integrations.fake.fake_gmail import FakeGmailService
from app.models.outbox import OutboxEvent
from app.models.tenant import get_tenant_context, set_tenant_context

logger = logging.getLogger("agentic_hr.outbox_publisher")


class OutboxDeliveryError(Exception):
    """Base exception for outbox event dispatch failures."""


class TransientDeliveryError(OutboxDeliveryError):
    """Raised when an external transient error occurs (e.g. rate limit, temporary timeout)."""


class PermanentDeliveryError(OutboxDeliveryError):
    """Raised when an event cannot be delivered due to invalid payload or fatal rejection."""


class FakeEventDispatcher:
    """
    In-memory fake event dispatcher for testing and local development.
    Enforces zero real external API calls (USE_FAKE_INTEGRATIONS=true).
    Maintains an auditable delivery log and deduplication registry.
    """

    def __init__(self) -> None:
        self.dispatched_events: list[dict[str, Any]] = []
        self.delivered_event_ids: set[UUID] = set()
        self.email_service = FakeGmailService()
        self.custom_handlers: dict[str, Callable[[OutboxEvent], Awaitable[bool]]] = {}

    def register_handler(
        self,
        event_type: str,
        handler: Callable[[OutboxEvent], Awaitable[bool]],
    ) -> None:
        """Allows test suites to register custom handlers (e.g. to simulate network failure)."""
        self.custom_handlers[event_type] = handler

    def clear(self) -> None:
        """Resets in-memory tracking between test runs."""
        self.dispatched_events.clear()
        self.delivered_event_ids.clear()
        self.custom_handlers.clear()

    async def dispatch(self, event: OutboxEvent) -> bool:
        """
        Dispatches an outbox event.
        Guarantees downstream idempotency using event.id.
        """
        if event.event_type in self.custom_handlers:
            return await self.custom_handlers[event.event_type](event)

        # Downstream deduplication check
        if event.id in self.delivered_event_ids:
            logger.info(
                f"Duplicate dispatch detected for event {event.id}; downstream deduplicated."
            )
            return True

        # Default domain event routing
        if event.event_type.startswith("approval."):
            if not settings.USE_FAKE_INTEGRATIONS:
                raise PermanentDeliveryError(
                    "FakeEventDispatcher cannot dispatch live emails when USE_FAKE_INTEGRATIONS=false; "
                    "must route through EmailDeliveryService / execute_connector_call."
                )
            payload = event.event_payload
            subject = f"[Action Required] Approval {event.event_type}: {payload.get('approval_type', 'General')}"
            recipient = payload.get("approver_email") or "agentic.hr.test.ubaid.owner@gmail.com"
            await self.email_service.send_email(
                EmailMessage(
                    to_email=recipient,
                    subject=subject,
                    body_text=f"Approval event {event.id} ({event.event_type}) has been processed.",
                )
            )

        self.delivered_event_ids.add(event.id)
        self.dispatched_events.append(
            {
                "event_id": event.id,
                "tenant_id": event.tenant_id,
                "event_type": event.event_type,
                "event_payload": event.event_payload,
                "timestamp": datetime.now(UTC),
            }
        )
        logger.info(f"Dispatched outbox event {event.id} (type: {event.event_type})")
        return True


# Global default fake dispatcher singleton
default_fake_dispatcher = FakeEventDispatcher()


async def fetch_due_tenant_ids(
    session: AsyncSession,
    limit: int = 50,
    stale_processing_seconds: int = 300,
) -> list[UUID]:
    """
    Discovers tenant UUIDs that currently have pending or stale processing outbox events
    using the narrow SECURITY DEFINER database function public.outbox_due_tenant_ids().
    """
    result = await session.execute(
        text("SELECT public.outbox_due_tenant_ids(:limit, :stale)"),
        {"limit": limit, "stale": stale_processing_seconds},
    )
    return [UUID(str(row[0])) for row in result.all() if row[0] is not None]


async def claim_pending_events(
    session: AsyncSession,
    tenant_id: UUID | str | None = None,
    batch_size: int = 10,
    stale_processing_seconds: int = 300,
) -> list[OutboxEvent]:
    """
    Safely claims pending or orphaned outbox events using SELECT ... FOR UPDATE SKIP LOCKED.
    Prevents lock contention and ensures multiple concurrent workers claim disjoint sets.
    Transitions claimed events to 'processing' and increments attempts counter.
    """
    active_tenant = str(tenant_id) if tenant_id else await get_tenant_context(session)
    if active_tenant:
        await set_tenant_context(session, active_tenant)

    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(seconds=stale_processing_seconds)

    conditions = [
        or_(
            and_(
                OutboxEvent.status == "pending",
                OutboxEvent.next_attempt_at <= now,
            ),
            and_(
                OutboxEvent.status == "processing",
                OutboxEvent.next_attempt_at <= stale_cutoff,
            ),
        )
    ]
    if active_tenant:
        conditions.append(OutboxEvent.tenant_id == UUID(active_tenant))

    stmt = (
        select(OutboxEvent)
        .where(and_(*conditions))
        .order_by(OutboxEvent.created_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    result = await session.execute(stmt)
    events = list(result.scalars().all())

    for event in events:
        event.status = "processing"
        event.attempts += 1
        event.next_attempt_at = now

    if events:
        await session.flush()

    return events


async def publish_single_event(
    session: AsyncSession,
    event: OutboxEvent,
    dispatcher: Callable[[OutboxEvent], Awaitable[bool]] | None = None,
    max_attempts: int = 5,
    base_backoff_seconds: float = 2.0,
) -> bool:
    """
    Dispatches a claimed outbox event.
    - On success: marks as 'published' and updates processed_at.
    - On permanent error: transitions immediately to 'failed' (dead letter).
    - On transient error: increments attempts and schedules exponential retry if attempts < max_attempts.
    Commits the status update to PostgreSQL.
    Re-establishes tenant context so RLS policies allow the UPDATE to succeed.
    """
    dispatch_fn = dispatcher or default_fake_dispatcher.dispatch
    now = datetime.now(UTC)

    # Re-establish RLS tenant context for this event's tenant in the active transaction
    await set_tenant_context(session, str(event.tenant_id))

    try:
        success = await dispatch_fn(event)
        if success:
            event.status = "published"
            event.processed_at = now
            event.last_error = None
            logger.info(f"Successfully published outbox event {event.id}")
            await session.commit()
            return True
        else:
            raise TransientDeliveryError(f"Dispatcher returned False for event {event.id}")

    except PermanentDeliveryError as fatal_err:
        await set_tenant_context(session, str(event.tenant_id))
        event.status = "failed"
        event.processed_at = now
        event.last_error = f"Permanent error: {fatal_err}"
        logger.error(f"Permanent delivery failure for outbox event {event.id}: {fatal_err}")
        await session.commit()
        return False

    except Exception as exc:
        await set_tenant_context(session, str(event.tenant_id))
        logger.warning(
            f"Transient delivery error on outbox event {event.id} (attempt {event.attempts}/{max_attempts}): {exc}"
        )
        if event.attempts < max_attempts:
            # Exponential backoff: base * 2^(attempts - 1)
            backoff_secs = base_backoff_seconds * (2 ** (event.attempts - 1))
            event.status = "pending"
            event.next_attempt_at = now + timedelta(seconds=backoff_secs)
            event.last_error = f"Transient error on attempt {event.attempts}: {exc}"
        else:
            # Retries exhausted -> Dead letter
            event.status = "failed"
            event.processed_at = now
            event.last_error = f"Exhausted {max_attempts} attempts. Last error: {exc}"
            logger.error(f"Outbox event {event.id} dead-lettered after {max_attempts} attempts.")

        await session.commit()
        return False


async def publish_batch(
    session: AsyncSession,
    tenant_id: UUID | str | None = None,
    batch_size: int = 10,
    dispatcher: Callable[[OutboxEvent], Awaitable[bool]] | None = None,
    max_attempts: int = 5,
    base_backoff_seconds: float = 2.0,
    stale_processing_seconds: int = 300,
) -> int:
    """
    Claims and processes a batch of eligible outbox events.
    Returns the count of events claimed and processed.
    """
    active_tenant = str(tenant_id) if tenant_id else await get_tenant_context(session)
    events = await claim_pending_events(
        session=session,
        tenant_id=active_tenant,
        batch_size=batch_size,
        stale_processing_seconds=stale_processing_seconds,
    )
    if not events:
        return 0

    # Commit claim phase so row locks are not held during external I/O
    await session.commit()

    processed_count = 0
    for event in events:
        await publish_single_event(
            session=session,
            event=event,
            dispatcher=dispatcher,
            max_attempts=max_attempts,
            base_backoff_seconds=base_backoff_seconds,
        )
        processed_count += 1

    return processed_count


class OutboxPublisherWorker:
    """
    Background worker loop that continuously drains pending events from the outbox.
    """

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        tenant_id: UUID | str | None = None,
        batch_size: int = 10,
        poll_interval_seconds: float = 1.0,
        dispatcher: Callable[[OutboxEvent], Awaitable[bool]] | None = None,
        max_attempts: int = 5,
        base_backoff_seconds: float = 2.0,
        stale_processing_seconds: int = 300,
    ) -> None:
        self.session_factory = session_factory
        self.tenant_id = UUID(str(tenant_id)) if tenant_id else None
        self.batch_size = batch_size
        self.poll_interval_seconds = poll_interval_seconds
        self.dispatcher = dispatcher
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds
        self.stale_processing_seconds = stale_processing_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> int:
        """
        Executes a single polling cycle over the outbox.
        If self.tenant_id is None, discovers all tenants with due outbox events via
        public.outbox_due_tenant_ids() and processes each under its strict RLS context.
        """
        async with self.session_factory() as session:
            if self.tenant_id is not None:
                return await publish_batch(
                    session=session,
                    tenant_id=self.tenant_id,
                    batch_size=self.batch_size,
                    dispatcher=self.dispatcher,
                    max_attempts=self.max_attempts,
                    base_backoff_seconds=self.base_backoff_seconds,
                    stale_processing_seconds=self.stale_processing_seconds,
                )

            due_tenants = await fetch_due_tenant_ids(
                session=session,
                limit=50,
                stale_processing_seconds=self.stale_processing_seconds,
            )
            total_processed = 0
            for t_id in due_tenants:
                count = await publish_batch(
                    session=session,
                    tenant_id=t_id,
                    batch_size=self.batch_size,
                    dispatcher=self.dispatcher,
                    max_attempts=self.max_attempts,
                    base_backoff_seconds=self.base_backoff_seconds,
                    stale_processing_seconds=self.stale_processing_seconds,
                )
                total_processed += count
            return total_processed

    async def start(self) -> None:
        """Starts the worker in the background."""
        self._running = True

        async def _loop() -> None:
            while self._running:
                try:
                    count = await self.run_once()
                    if count == 0:
                        await asyncio.sleep(self.poll_interval_seconds)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error(f"Unhandled error in OutboxPublisherWorker loop: {exc}")
                    await asyncio.sleep(self.poll_interval_seconds)

        self._task = asyncio.create_task(_loop())
        logger.info("OutboxPublisherWorker started.")

    async def stop(self) -> None:
        """Gracefully halts the background worker."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("OutboxPublisherWorker stopped.")
