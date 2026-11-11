"""
Google Calendar Orchestration Service for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and ADR 006 (calendar.events scope least privilege).
Enforces default timezone: Asia/Karachi (PKT, UTC+05:00).
Enforces IdempotencyGate, Read-Only mode, Redis rate limits, and audit logging.
"""

import logging
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import IdempotencyGate, compute_payload_hash
from app.integrations.base import (
    CalendarEvent,
    CalendarEventUpdate,
    TimeSlot,
)
from app.integrations.factory import get_calendar_service
from app.integrations.google.calendar_client import (
    DEFAULT_TIMEZONE,
    normalize_to_karachi_tz,
)
from app.models.calendar import CalendarEventRecord
from app.services.connector_call_wrapper import execute_connector_call
from app.services.connector_service import ConnectorService

logger = logging.getLogger("agentic_hr.services.calendar")


class CalendarSchedulingService:
    """Orchestrates Google Calendar events and scheduling under calendar.events scope."""

    @staticmethod
    async def get_free_busy(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
    ) -> dict[str, list[TimeSlot]]:
        """Queries free/busy windows for given calendar IDs in Asia/Karachi timezone. Read-only."""
        t_id = UUID(str(tenant_id))
        if credential_id:
            cred = await ConnectorService.get_credential_by_id(session, t_id, credential_id)
        else:
            cred = await ConnectorService.get_active_credential(session, t_id, provider="google")

        cal_svc = get_calendar_service()

        return await execute_connector_call(
            session=session,
            tenant_id=t_id,
            credential=cred,
            operation="calendar.free_busy",
            is_write=False,  # Allowed in read-only mode
            call_factory=lambda token: cal_svc.get_free_busy(
                calendar_ids=calendar_ids,
                time_min=time_min,
                time_max=time_max,
                access_token=token,
            ),
            actor_id=actor_id,
            rate_limit_scope="calendar.events",
            max_requests_per_minute=30,
        )

    @staticmethod
    async def check_conflicts(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        calendar_ids: list[str],
        start_time: datetime,
        end_time: datetime,
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
    ) -> bool:
        """Returns True if any busy slot conflicts with the requested window."""
        t_id = UUID(str(tenant_id))
        if credential_id:
            cred = await ConnectorService.get_credential_by_id(session, t_id, credential_id)
        else:
            cred = await ConnectorService.get_active_credential(session, t_id, provider="google")

        cal_svc = get_calendar_service()

        return await execute_connector_call(
            session=session,
            tenant_id=t_id,
            credential=cred,
            operation="calendar.free_busy",
            is_write=False,
            call_factory=lambda token: cal_svc.check_conflicts(
                calendar_ids=calendar_ids,
                start_time=start_time,
                end_time=end_time,
                access_token=token,
            ),
            actor_id=actor_id,
            rate_limit_scope="calendar.events",
            max_requests_per_minute=30,
        )

    @staticmethod
    async def create_event(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        title: str,
        start_time: datetime,
        end_time: datetime,
        attendees: list[str] | None = None,
        description: str | None = None,
        location: str | None = "Google Meet",
        calendar_id: str = "primary",
        timezone: str = DEFAULT_TIMEZONE,
        idempotency_key: str | None = None,
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
    ) -> CalendarEventRecord:
        """Creates a calendar event (with Google Meet link) normalized to Asia/Karachi timezone."""
        t_id = UUID(str(tenant_id))
        s_norm = normalize_to_karachi_tz(start_time, timezone)
        e_norm = normalize_to_karachi_tz(end_time, timezone)
        clean_attendees = [a.strip().lower() for a in (attendees or [])]

        if credential_id:
            cred = await ConnectorService.get_credential_by_id(session, t_id, credential_id)
        else:
            cred = await ConnectorService.get_active_credential(session, t_id, provider="google")

        # Enforce IdempotencyGate
        effective_key = idempotency_key or f"auto_cal_{uuid.uuid4()}"
        payload_hash = compute_payload_hash(
            {
                "title": title,
                "start": s_norm.isoformat(),
                "end": e_norm.isoformat(),
                "attendees": clean_attendees,
                "credential_id": str(cred.id),
            }
        )

        async with IdempotencyGate(
            session=session,
            tenant_id=t_id,
            operation="calendar.create_event",
            idempotency_key=effective_key,
            payload_hash=payload_hash,
        ) as gate:
            if gate.is_replay:
                cached_id = gate.response_body.get("id")
                if cached_id:
                    stmt = select(CalendarEventRecord).where(
                        CalendarEventRecord.tenant_id == t_id,
                        CalendarEventRecord.id == UUID(cached_id),
                    )
                    res = await session.execute(stmt)
                    cached_record = res.scalars().first()
                    if cached_record:
                        return cached_record

            event_obj = CalendarEvent(
                title=title,
                start_time=s_norm,
                end_time=e_norm,
                attendees=clean_attendees,
                description=description,
                location=location,
                calendar_id=calendar_id,
            )

            cal_svc = get_calendar_service()
            result: dict[str, Any] = await execute_connector_call(
                session=session,
                tenant_id=t_id,
                credential=cred,
                operation="calendar.create_event",
                is_write=True,
                call_factory=lambda token: cal_svc.create_event(event_obj, access_token=token),
                actor_id=actor_id,
                rate_limit_scope="calendar.events",
                max_requests_per_minute=30,
                audit_target_type="connector_credential",
                audit_target_id=cred.id,
                audit_details={
                    "title": title,
                    "start": s_norm.isoformat(),
                    "end": e_norm.isoformat(),
                },
            )

            google_event_id = result.get("event_id") or str(uuid.uuid4())
            hangout_link = result.get("hangout_link")

            record = CalendarEventRecord(
                tenant_id=t_id,
                credential_id=cred.id,
                google_event_id=google_event_id,
                title=title,
                description=description,
                location=location,
                hangout_link=hangout_link,
                start_time=s_norm,
                end_time=e_norm,
                timezone=timezone,
                attendees=clean_attendees,
                status="confirmed",
            )
            session.add(record)
            await session.flush()

            response_data = {
                "id": str(record.id),
                "google_event_id": record.google_event_id,
                "title": record.title,
                "start_time": record.start_time.isoformat(),
                "end_time": record.end_time.isoformat(),
                "timezone": record.timezone,
                "hangout_link": record.hangout_link,
            }
            gate.set_result(200, response_data)
            return record

    @staticmethod
    async def update_event(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        event_id: str,
        title: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        attendees: list[str] | None = None,
        description: str | None = None,
        location: str | None = None,
        calendar_id: str = "primary",
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
    ) -> CalendarEventRecord:
        """Updates/reschedules an existing calendar event."""
        t_id = UUID(str(tenant_id))
        if credential_id:
            cred = await ConnectorService.get_credential_by_id(session, t_id, credential_id)
        else:
            cred = await ConnectorService.get_active_credential(session, t_id, provider="google")

        # Locate database record
        stmt = select(CalendarEventRecord).where(
            CalendarEventRecord.tenant_id == t_id,
            (CalendarEventRecord.google_event_id == event_id)
            | (CalendarEventRecord.id == UUID(event_id) if event_id.count("-") == 4 else False),
        )
        res = await session.execute(stmt)
        record = res.scalars().first()
        target_event_id = record.google_event_id if record else event_id

        s_norm = normalize_to_karachi_tz(start_time) if start_time else None
        e_norm = normalize_to_karachi_tz(end_time) if end_time else None
        clean_attendees = [a.strip().lower() for a in attendees] if attendees is not None else None

        update_dto = CalendarEventUpdate(
            title=title,
            start_time=s_norm,
            end_time=e_norm,
            attendees=clean_attendees,
            description=description,
            location=location,
        )

        cal_svc = get_calendar_service()
        await execute_connector_call(
            session=session,
            tenant_id=t_id,
            credential=cred,
            operation="calendar.update_event",
            is_write=True,
            call_factory=lambda token: cal_svc.update_event(
                target_event_id, update_dto, access_token=token
            ),
            actor_id=actor_id,
            rate_limit_scope="calendar.events",
            max_requests_per_minute=30,
            audit_target_type="connector_credential",
            audit_target_id=cred.id,
            audit_details={"google_event_id": target_event_id, "title": title},
        )

        if record:
            if title is not None:
                record.title = title
            if s_norm is not None:
                record.start_time = s_norm
            if e_norm is not None:
                record.end_time = e_norm
            if clean_attendees is not None:
                record.attendees = clean_attendees
            if description is not None:
                record.description = description
            if location is not None:
                record.location = location
            await session.flush()
        else:
            # Create local representation if not already tracked
            record = CalendarEventRecord(
                tenant_id=t_id,
                credential_id=cred.id,
                google_event_id=target_event_id,
                title=title or "Updated Event",
                start_time=s_norm or datetime.now(),
                end_time=e_norm or datetime.now(),
                attendees=clean_attendees or [],
                status="confirmed",
            )
            session.add(record)
            await session.flush()

        return record

    @staticmethod
    async def cancel_event(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        event_id: str,
        calendar_id: str = "primary",
        credential_id: UUID | str | None = None,
        actor_id: UUID | str | None = None,
    ) -> bool:
        """Cancels an existing calendar event."""
        t_id = UUID(str(tenant_id))
        if credential_id:
            cred = await ConnectorService.get_credential_by_id(session, t_id, credential_id)
        else:
            cred = await ConnectorService.get_active_credential(session, t_id, provider="google")

        stmt = select(CalendarEventRecord).where(
            CalendarEventRecord.tenant_id == t_id,
            (CalendarEventRecord.google_event_id == event_id)
            | (CalendarEventRecord.id == UUID(event_id) if event_id.count("-") == 4 else False),
        )
        res = await session.execute(stmt)
        record = res.scalars().first()
        target_event_id = record.google_event_id if record else event_id

        cal_svc = get_calendar_service()
        deleted = await execute_connector_call(
            session=session,
            tenant_id=t_id,
            credential=cred,
            operation="calendar.cancel_event",
            is_write=True,
            call_factory=lambda token: cal_svc.cancel_event(target_event_id, access_token=token),
            actor_id=actor_id,
            rate_limit_scope="calendar.events",
            max_requests_per_minute=30,
            audit_target_type="connector_credential",
            audit_target_id=cred.id,
            audit_details={"google_event_id": target_event_id},
        )

        if record:
            record.status = "cancelled"
            await session.flush()

        return deleted
