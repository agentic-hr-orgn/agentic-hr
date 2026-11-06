from typing import Any

from app.integrations.base import CalendarEvent, CalendarService


class GoogleCalendarService(CalendarService):
    """
    Real Google Calendar service client.
    Scope: https://www.googleapis.com/auth/calendar.events ONLY per ADR 006.
    """

    ALLOWED_SCOPE = "https://www.googleapis.com/auth/calendar.events"

    def __init__(self, credentials: Any = None) -> None:
        self.credentials = credentials

    async def create_event(self, event: CalendarEvent) -> dict[str, Any]:
        if not self.credentials:
            raise RuntimeError(
                "Real Google Calendar integration requires valid credentials. "
                "In Stage 1, ensure USE_FAKE_INTEGRATIONS=true."
            )
        raise NotImplementedError("Real Calendar event creation active in later stages.")
