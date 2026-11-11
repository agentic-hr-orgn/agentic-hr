"""
Pydantic Schemas for Google Calendar Connector Endpoints in Agentic HR Core.
Scope: https://www.googleapis.com/auth/calendar.events per ADR 006.
Enforces default timezone: Asia/Karachi (PKT, UTC+05:00).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TimeSlotSchema(BaseModel):
    start_time: datetime
    end_time: datetime


class FreeBusyQueryRequest(BaseModel):
    calendar_ids: list[str] = Field(default_factory=lambda: ["primary"])
    time_min: datetime
    time_max: datetime


class FreeBusyQueryResponse(BaseModel):
    calendars: dict[str, list[TimeSlotSchema]]


class CalendarEventCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    start_time: datetime
    end_time: datetime
    attendees: list[str] = Field(default_factory=list)
    description: str | None = None
    location: str | None = "Google Meet"
    calendar_id: str = "primary"
    timezone: str = "Asia/Karachi"


class CalendarEventUpdateRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    start_time: datetime | None = None
    end_time: datetime | None = None
    attendees: list[str] | None = None
    description: str | None = None
    location: str | None = None


class CalendarEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    google_event_id: str
    title: str
    start_time: datetime
    end_time: datetime
    timezone: str
    attendees: list[str]
    description: str | None = None
    location: str | None = None
    hangout_link: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
