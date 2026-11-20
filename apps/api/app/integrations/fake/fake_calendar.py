"""
Fake Google Calendar Service with in-memory storage and deterministic fault injection.
Enforces ADR 006: calendar.events scope least privilege.
"""

import uuid
from datetime import datetime
from typing import Any

from app.core.timezones import (
    DEFAULT_TIMEZONE,
    get_karachi_tz,
    normalize_to_karachi_tz,
)
from app.integrations.base import (
    CalendarEvent,
    CalendarEventUpdate,
    CalendarService,
    TimeSlot,
)
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorRateLimitError,
    ConnectorServiceUnavailableError,
)

__all__ = [
    "DEFAULT_TIMEZONE",
    "FakeCalendarService",
    "get_karachi_tz",
    "normalize_to_karachi_tz",
]


class FakeCalendarService(CalendarService):
    """
    Fake Google Calendar service recording scheduled events in memory.
    Enforces ADR 006: Stage 1/3 USE_FAKE_INTEGRATIONS=true, no real calendar events.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.simulate_401_on_next_call: bool = False
        self.simulate_429_on_next_call: bool = False
        self.simulate_503_on_next_call: bool = False

    def reset(self) -> None:
        """Resets in-memory events and fault injection flags."""
        self.events.clear()
        self.simulate_401_on_next_call = False
        self.simulate_429_on_next_call = False
        self.simulate_503_on_next_call = False

    def _check_fault_injection(self) -> None:
        if self.simulate_401_on_next_call:
            self.simulate_401_on_next_call = False
            raise ConnectorAuthExpiredError("Simulated Google Calendar 401 Unauthorized")
        if self.simulate_429_on_next_call:
            self.simulate_429_on_next_call = False
            raise ConnectorRateLimitError(
                "Simulated Google Calendar 429 Too Many Requests", retry_after=1
            )
        if self.simulate_503_on_next_call:
            self.simulate_503_on_next_call = False
            raise ConnectorServiceUnavailableError(
                "Simulated Google Calendar 503 Service Unavailable"
            )

    async def get_free_busy(
        self,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        access_token: str | None = None,
    ) -> dict[str, list[TimeSlot]]:
        """Queries free/busy windows for given calendar IDs within the time window."""
        self._check_fault_injection()

        t_min = normalize_to_karachi_tz(time_min)
        t_max = normalize_to_karachi_tz(time_max)
        busy_map: dict[str, list[TimeSlot]] = {cal_id: [] for cal_id in calendar_ids}
        for ev in self.events:
            ev_start = normalize_to_karachi_tz(datetime.fromisoformat(ev["start"]))
            ev_end = normalize_to_karachi_tz(datetime.fromisoformat(ev["end"]))
            # Check overlap with query window
            if ev_start < t_max and ev_end > t_min:
                slot = TimeSlot(start_time=ev_start, end_time=ev_end)
                for cal_id in calendar_ids:
                    if cal_id == ev.get("calendar_id", "primary") or cal_id in ev.get(
                        "attendees", []
                    ):
                        busy_map[cal_id].append(slot)
        return busy_map

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
        """Creates an interview or meeting event in Asia/Karachi timezone."""
        self._check_fault_injection()

        s_time = normalize_to_karachi_tz(event.start_time)
        e_time = normalize_to_karachi_tz(event.end_time)

        event_id = event.event_id or f"fake_event_{uuid.uuid4().hex[:8]}"
        record = {
            "id": event_id,
            "title": event.title,
            "start": s_time.isoformat(),
            "end": e_time.isoformat(),
            "timezone": DEFAULT_TIMEZONE,
            "attendees": event.attendees,
            "description": event.description,
            "location": event.location,
            "calendar_id": event.calendar_id,
            "status": "confirmed",
            "hangout_link": f"https://meet.google.com/fake-{uuid.uuid4().hex[:4]}",
        }
        self.events.append(record)
        return {
            "event_id": event_id,
            "status": "confirmed",
            "fake": True,
            "hangout_link": record["hangout_link"],
            "details": record,
        }

    async def update_event(
        self,
        event_id: str,
        event: CalendarEventUpdate,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """Updates/reschedules an existing calendar event."""
        self._check_fault_injection()

        for ev in self.events:
            if ev["id"] == event_id:
                if event.title is not None:
                    ev["title"] = event.title
                if event.start_time is not None:
                    ev["start"] = normalize_to_karachi_tz(event.start_time).isoformat()
                if event.end_time is not None:
                    ev["end"] = normalize_to_karachi_tz(event.end_time).isoformat()
                if event.attendees is not None:
                    ev["attendees"] = event.attendees
                if event.description is not None:
                    ev["description"] = event.description
                if event.location is not None:
                    ev["location"] = event.location
                return {
                    "event_id": event_id,
                    "status": "updated",
                    "fake": True,
                    "details": ev,
                }
        raise ValueError(f"Event with ID '{event_id}' not found.")

    async def cancel_event(
        self,
        event_id: str,
        access_token: str | None = None,
    ) -> bool:
        """Cancels/deletes an existing calendar event."""
        self._check_fault_injection()

        for i, ev in enumerate(self.events):
            if ev["id"] == event_id:
                self.events.pop(i)
                return True
        return False
