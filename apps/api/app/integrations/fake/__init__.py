"""Fake integration implementations for Stage 1 (ADR 006)."""

from app.integrations.fake.fake_calendar import FakeCalendarService
from app.integrations.fake.fake_drive import FakeDriveService
from app.integrations.fake.fake_gmail import FakeGmailService
from app.integrations.fake.fake_sheets import FakeSheetsService

__all__ = [
    "FakeCalendarService",
    "FakeDriveService",
    "FakeGmailService",
    "FakeSheetsService",
]
