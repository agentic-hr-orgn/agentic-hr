"""
Google Calendar Service Client (Real HTTP/API Implementation).
Scope: https://www.googleapis.com/auth/calendar.events ONLY per ADR 006.
Mandatory Default Timezone: Asia/Karachi (PKT, UTC+05:00).
"""

import logging
import uuid
import zoneinfo
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.integrations.base import (
    CalendarEvent,
    CalendarEventUpdate,
    CalendarService,
    TimeSlot,
)
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorError,
    ConnectorRateLimitError,
    ConnectorServiceUnavailableError,
)

logger = logging.getLogger("agentic_hr.integrations.google.calendar")

DEFAULT_TIMEZONE = "Asia/Karachi"


def get_karachi_tz(tz_name: str = DEFAULT_TIMEZONE) -> datetime.tzinfo:
    """Returns zoneinfo timezone if available, otherwise fixed UTC+05:00 offset."""
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return timezone(timedelta(hours=5), name=tz_name)


def normalize_to_karachi_tz(dt: datetime, default_tz: str = DEFAULT_TIMEZONE) -> datetime:
    """Normalizes naive or foreign datetime objects to Asia/Karachi (PKT, UTC+05:00)."""
    target_tz = get_karachi_tz(default_tz)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=target_tz)
    return dt.astimezone(target_tz)


class GoogleCalendarService(CalendarService):
    """
    Real Google Calendar service client via Google Calendar API v3.
    Scope: https://www.googleapis.com/auth/calendar.events ONLY per ADR 006.
    """

    ALLOWED_SCOPE = "https://www.googleapis.com/auth/calendar.events"

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def _handle_error_response(self, resp: httpx.Response, action: str) -> None:
        if resp.status_code == 401 or "invalid_grant" in resp.text:
            raise ConnectorAuthExpiredError(
                f"Google Calendar API 401 Unauthorized during {action}: {resp.text}"
            )
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 1))
            raise ConnectorRateLimitError(
                f"Google Calendar API 429 Rate Limited during {action}: {resp.text}",
                retry_after=retry_after,
            )
        if resp.status_code in (500, 502, 503, 504):
            raise ConnectorServiceUnavailableError(
                f"Google Calendar API {resp.status_code} Service Unavailable during {action}: {resp.text}"
            )
        if resp.status_code == 404:
            raise ValueError(f"Google Calendar resource not found during {action}: {resp.text}")
        if resp.status_code >= 400:
            raise ConnectorError(
                f"Google Calendar API error ({resp.status_code}) during {action}: {resp.text}"
            )

    async def get_free_busy(
        self,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        access_token: str | None = None,
    ) -> dict[str, list[TimeSlot]]:
        """Queries free/busy windows for given calendar IDs within the time window."""
        t_min = normalize_to_karachi_tz(time_min)
        t_max = normalize_to_karachi_tz(time_max)

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "timeMin": t_min.isoformat(),
            "timeMax": t_max.isoformat(),
            "timeZone": DEFAULT_TIMEZONE,
            "items": [{"id": cid} for cid in calendar_ids],
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                "https://www.googleapis.com/calendar/v3/freeBusy",
                headers=headers,
                json=payload,
            )
            self._handle_error_response(resp, "get_free_busy")
            data = resp.json()

            calendars_data = data.get("calendars", {})
            result: dict[str, list[TimeSlot]] = {}
            for cid in calendar_ids:
                busy_list = calendars_data.get(cid, {}).get("busy", [])
                slots = [
                    TimeSlot(
                        start_time=datetime.fromisoformat(b["start"]),
                        end_time=datetime.fromisoformat(b["end"]),
                    )
                    for b in busy_list
                ]
                result[cid] = slots
            return result

    async def check_conflicts(
        self,
        calendar_ids: list[str],
        start_time: datetime,
        end_time: datetime,
        access_token: str | None = None,
    ) -> bool:
        """Returns True if any busy slot conflicts with the requested window."""
        free_busy = await self.get_free_busy(
            calendar_ids, time_min=start_time, time_max=end_time, access_token=access_token
        )
        for busy_slots in free_busy.values():
            if len(busy_slots) > 0:
                return True
        return False

    async def create_event(
        self,
        event: CalendarEvent,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Creates an interview or meeting event with Google Meet link in Asia/Karachi timezone."""
        s_time = normalize_to_karachi_tz(event.start_time)
        e_time = normalize_to_karachi_tz(event.end_time)

        cal_id = event.calendar_id or "primary"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        req_body: dict[str, Any] = {
            "summary": event.title,
            "start": {"dateTime": s_time.isoformat(), "timeZone": DEFAULT_TIMEZONE},
            "end": {"dateTime": e_time.isoformat(), "timeZone": DEFAULT_TIMEZONE},
            "attendees": [{"email": a} for a in event.attendees],
            "conferenceData": {
                "createRequest": {
                    "requestId": f"meet_{uuid.uuid4().hex[:12]}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        if event.description:
            req_body["description"] = event.description
        if event.location:
            req_body["location"] = event.location

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events",
                headers=headers,
                params={"conferenceDataVersion": 1},
                json=req_body,
            )
            self._handle_error_response(resp, "create_event")
            data = resp.json()

            hangout_link = data.get("hangoutLink")
            if not hangout_link and data.get("conferenceData", {}).get("entryPoints"):
                for ep in data["conferenceData"]["entryPoints"]:
                    if ep.get("entryPointType") == "video":
                        hangout_link = ep.get("uri")
                        break

            return {
                "event_id": data["id"],
                "status": data.get("status", "confirmed"),
                "hangout_link": hangout_link,
                "fake": False,
                "details": data,
            }

    async def update_event(
        self,
        event_id: str,
        event: CalendarEventUpdate,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Updates or reschedules an existing calendar event."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        patch_body: dict[str, Any] = {}
        if event.title is not None:
            patch_body["summary"] = event.title
        if event.description is not None:
            patch_body["description"] = event.description
        if event.location is not None:
            patch_body["location"] = event.location
        if event.start_time is not None:
            s_time = normalize_to_karachi_tz(event.start_time)
            patch_body["start"] = {"dateTime": s_time.isoformat(), "timeZone": DEFAULT_TIMEZONE}
        if event.end_time is not None:
            e_time = normalize_to_karachi_tz(event.end_time)
            patch_body["end"] = {"dateTime": e_time.isoformat(), "timeZone": DEFAULT_TIMEZONE}
        if event.attendees is not None:
            patch_body["attendees"] = [{"email": a} for a in event.attendees]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.patch(
                f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
                headers=headers,
                json=patch_body,
            )
            self._handle_error_response(resp, "update_event")
            data = resp.json()
            return {
                "event_id": event_id,
                "status": data.get("status", "updated"),
                "fake": False,
                "details": data,
            }

    async def cancel_event(
        self,
        event_id: str,
        access_token: str | None = None,
    ) -> bool:
        """Cancels or deletes an existing calendar event."""
        headers = {
            "Authorization": f"Bearer {access_token}",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.delete(
                f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
                headers=headers,
            )
            if resp.status_code in (200, 204):
                return True
            self._handle_error_response(resp, "cancel_event")
            return False
