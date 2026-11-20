"""
Google Calendar FastMCP Server for Agentic HR Core.
Exposes get_calendar_free_busy_tool, create_calendar_event_tool,
update_calendar_event_tool, and cancel_calendar_event_tool under ADR 006 calendar.events scope.
Enforces Asia/Karachi (PKT, UTC+05:00) normalization and Google Meet link generation.
"""

from datetime import datetime
from typing import Any

from app.core.timezones import iso_pkt
from app.mcp.base import FastMCP
from app.mcp.context import get_current_mcp_context, get_mcp_session
from app.mcp.guardrails import mcp_guardrail
from app.services.calendar_service import CalendarSchedulingService

calendar_mcp_server = FastMCP(
    name="agentic-hr-calendar",
    instructions="Google Calendar connector tools for scheduling candidate interviews, panel sessions, and querying availability in Asia/Karachi timezone.",
)


@calendar_mcp_server.tool(
    name="get_calendar_free_busy_tool",
    description="Queries free/busy availability for attendee calendars. Normalized to Asia/Karachi timezone.",
)
@mcp_guardrail("get_calendar_free_busy_tool", is_write=False)
async def get_calendar_free_busy_tool(
    calendar_ids: list[str],
    time_min: str,
    time_max: str,
) -> dict[str, list[dict[str, str]]]:
    """
    Returns busy time slots for requested calendar IDs.
    """
    ctx = get_current_mcp_context()
    dt_min = datetime.fromisoformat(time_min)
    dt_max = datetime.fromisoformat(time_max)

    async with get_mcp_session(ctx) as session:
        free_busy_map = await CalendarSchedulingService.get_free_busy(
            session=session,
            tenant_id=ctx.tenant_id,
            calendar_ids=calendar_ids,
            time_min=dt_min,
            time_max=dt_max,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return {
            cal_id: [
                {
                    "start_time": iso_pkt(slot.start_time) or "",
                    "end_time": iso_pkt(slot.end_time) or "",
                }
                for slot in slots
            ]
            for cal_id, slots in free_busy_map.items()
        }


@calendar_mcp_server.tool(
    name="create_calendar_event_tool",
    description="Schedules a calendar event with automatic Google Meet link generation in Asia/Karachi timezone. Enforces read-only check and idempotency.",
)
@mcp_guardrail("create_calendar_event_tool", is_write=True)
async def create_calendar_event_tool(
    title: str,
    start_time: str,
    end_time: str,
    attendees: list[str] | None = None,
    description: str | None = None,
    location: str | None = None,
    calendar_id: str = "primary",
    timezone: str = "Asia/Karachi",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """
    Creates a calendar event with Meet link.
    """
    ctx = get_current_mcp_context()
    dt_start = datetime.fromisoformat(start_time)
    dt_end = datetime.fromisoformat(end_time)

    async with get_mcp_session(ctx) as session:
        record = await CalendarSchedulingService.create_event(
            session=session,
            tenant_id=ctx.tenant_id,
            title=title,
            start_time=dt_start,
            end_time=dt_end,
            attendees=attendees,
            description=description,
            location=location,
            calendar_id=calendar_id,
            timezone=timezone,
            idempotency_key=idempotency_key,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return {
            "id": str(record.id),
            "calendar_event_id": record.google_event_id,
            "google_event_id": record.google_event_id,
            "title": record.title,
            "description": record.description,
            "location": record.location,
            "meeting_link": record.hangout_link,
            "hangout_link": record.hangout_link,
            "start_time": iso_pkt(record.start_time),
            "end_time": iso_pkt(record.end_time),
            "timezone": record.timezone,
            "attendees": record.attendees,
            "status": record.status,
            "created_at": iso_pkt(record.created_at),
        }


@calendar_mcp_server.tool(
    name="update_calendar_event_tool",
    description="Updates an existing calendar event timing, attendees, title, or description in Asia/Karachi timezone.",
)
@mcp_guardrail("update_calendar_event_tool", is_write=True)
async def update_calendar_event_tool(
    event_id: str,
    calendar_id: str = "primary",
    title: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
) -> dict[str, Any]:
    """
    Updates calendar event details.
    """
    ctx = get_current_mcp_context()
    dt_start = datetime.fromisoformat(start_time) if start_time else None
    dt_end = datetime.fromisoformat(end_time) if end_time else None

    async with get_mcp_session(ctx) as session:
        record = await CalendarSchedulingService.update_event(
            session=session,
            tenant_id=ctx.tenant_id,
            event_id=event_id,
            calendar_id=calendar_id,
            title=title,
            start_time=dt_start,
            end_time=dt_end,
            description=description,
            location=location,
            attendees=attendees,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return {
            "id": str(record.id),
            "calendar_event_id": record.google_event_id,
            "google_event_id": record.google_event_id,
            "title": record.title,
            "description": record.description,
            "location": record.location,
            "meeting_link": record.hangout_link,
            "hangout_link": record.hangout_link,
            "start_time": iso_pkt(record.start_time),
            "end_time": iso_pkt(record.end_time),
            "timezone": record.timezone,
            "attendees": record.attendees,
            "status": record.status,
            "updated_at": iso_pkt(record.updated_at),
        }


@calendar_mcp_server.tool(
    name="cancel_calendar_event_tool",
    description="Cancels an interview schedule event in Google Calendar.",
)
@mcp_guardrail("cancel_calendar_event_tool", is_write=True)
async def cancel_calendar_event_tool(
    event_id: str,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """
    Cancels a calendar event.
    """
    ctx = get_current_mcp_context()
    async with get_mcp_session(ctx) as session:
        success = await CalendarSchedulingService.cancel_event(
            session=session,
            tenant_id=ctx.tenant_id,
            event_id=event_id,
            calendar_id=calendar_id,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
        )
        return {
            "event_id": event_id,
            "status": "cancelled" if success else "failed",
            "success": bool(success),
        }
