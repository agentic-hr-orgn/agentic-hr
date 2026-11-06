from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EmailMessage(BaseModel):
    to_email: str
    subject: str
    body_text: str
    body_html: str | None = None
    sender_email: str | None = None


class CalendarEvent(BaseModel):
    title: str
    start_time: datetime
    end_time: datetime
    attendees: list[str]
    description: str | None = None
    location: str | None = "Google Meet"


class DriveFile(BaseModel):
    file_id: str
    name: str
    mime_type: str
    size_bytes: int
    web_view_link: str | None = None


class EmailService(ABC):
    """Abstract Email service restricted to SEND-ONLY per ADR 006 & docs/oauth-scopes.md."""

    @abstractmethod
    async def send_email(self, message: EmailMessage) -> dict[str, Any]:
        """Send an email. Reading or modifying inbox is strictly disallowed."""


class CalendarService(ABC):
    """Abstract Calendar service restricted to calendar.events per ADR 006."""

    @abstractmethod
    async def create_event(self, event: CalendarEvent) -> dict[str, Any]:
        """Create an interview or meeting event."""


class DriveService(ABC):
    """Abstract Drive service restricted to drive.file per ADR 006."""

    @abstractmethod
    async def upload_file(
        self,
        name: str,
        content: bytes,
        mime_type: str,
        folder_id: str | None = None,
    ) -> DriveFile:
        """Upload a file (e.g., CV) created or opened by the app."""


class SheetsService(ABC):
    """Abstract Sheets service treating Sheets as a Synced View per ADR 005."""

    @abstractmethod
    async def get_employee_directory(
        self, spreadsheet_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch employee directory from synced view."""
