import uuid
from typing import Any

from app.integrations.base import CalendarEvent, CalendarService


class FakeCalendarService(CalendarService):
    """
    Fake Google Calendar service recording scheduled events in memory.
    Enforces ADR 006: Stage 1 USE_FAKE_INTEGRATIONS=true, no real calendar events.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def create_event(self, event: CalendarEvent) -> dict[str, Any]:
        event_id = f"fake_event_{uuid.uuid4().hex[:8]}"
        record = {
            "id": event_id,
            "title": event.title,
            "start": event.start_time.isoformat(),
            "end": event.end_time.isoformat(),
            "attendees": event.attendees,
            "description": event.description,
            "location": event.location,
            "hangout_link": f"https://meet.google.com/fake-{uuid.uuid4().hex[:4]}",
        }
        self.events.append(record)
        return {
            "event_id": event_id,
            "status": "confirmed",
            "fake": True,
            "details": record,
        }
