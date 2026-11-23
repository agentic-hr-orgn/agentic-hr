"""
Pydantic Schemas for Stage 7 Helpdesk Tickets, Messages & Category Assignment Rules.
Complies with ADR 010 (SLA lifecycle, internal note isolation & pub_ticket_summary).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.stage7_common import (
    ChatLanguage,
    CitationItem,
    TicketCategory,
    TicketPriority,
)

TicketStatus = Literal["open", "in_progress", "waiting_on_employee", "resolved", "closed"]
TicketSource = Literal["chat_abstain", "chat_ask_hr", "helpdesk_agent", "manual"]
AssigneeRole = Literal["hr_head", "owner", "manager"]


class TicketCreateRequest(BaseModel):
    """Request payload to create an HR/IT/Facilities Helpdesk ticket."""

    category: TicketCategory = "general"
    priority: TicketPriority = "medium"
    subject: str = Field(..., min_length=3, max_length=255)
    description: str = Field(..., min_length=3, max_length=10000)
    source: TicketSource = "manual"
    source_session_id: UUID | None = None
    assigned_to_user_id: UUID | None = None
    start_workflow: bool = True
    reminder_delay_seconds: float | None = Field(default=None, gt=0)
    breach_delay_seconds: float | None = Field(default=None, gt=0)


class TicketUpdateRequest(BaseModel):
    """Handler update payload for ticket assignment, priority, or status."""

    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    category: TicketCategory | None = None
    assigned_to_user_id: UUID | None = None


class TicketResolveRequest(BaseModel):
    """Optional resolution note when transitioning a ticket to `resolved`."""

    resolution_note: str | None = Field(default=None, max_length=5000)


class TicketCloseRequest(BaseModel):
    """Requester close & CSAT rating payload (`satisfaction_rating` 1..5)."""

    satisfaction_rating: int | None = Field(default=None, ge=1, le=5)
    satisfaction_comment: str | None = Field(default=None, max_length=2000)


class TicketReopenRequest(BaseModel):
    """Requester reopen payload within the rule's `reopen_window_days`."""

    reason: str = Field(..., min_length=3, max_length=2000)
    start_workflow: bool = True
    reminder_delay_seconds: float | None = Field(default=None, gt=0)
    breach_delay_seconds: float | None = Field(default=None, gt=0)


class TicketMessageCreateRequest(BaseModel):
    """Payload to append a public reply or HR-only internal note to a ticket."""

    body: str = Field(..., min_length=1, max_length=10000)
    is_internal: bool = False


class TicketMessageResponse(BaseModel):
    """Serialized ticket message row (internal notes stripped for unauthorized callers)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    ticket_id: UUID
    author_user_id: UUID
    body: str
    is_internal: bool
    created_at: datetime


class TicketResponse(BaseModel):
    """Full tenant-scoped ticket metadata response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    ticket_number: str
    requester_user_id: UUID
    assigned_to_user_id: UUID | None = None
    category: TicketCategory
    priority: TicketPriority
    status: TicketStatus
    subject: str
    description: str
    source: TicketSource
    source_session_id: UUID | None = None
    sla_due_at: datetime
    sla_reminder_at: datetime
    sla_reminder_sent_at: datetime | None = None
    sla_escalated_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    reopen_until: datetime | None = None
    satisfaction_rating: int | None = None
    satisfaction_comment: str | None = None
    created_at: datetime
    updated_at: datetime


class TicketDetailResponse(TicketResponse):
    """Ticket detail envelope including authorized thread messages."""

    messages: list[TicketMessageResponse] = Field(default_factory=list)


class TicketSummaryResponse(BaseModel):
    """Serialized projection matching secure view `public.pub_ticket_summary`."""

    model_config = ConfigDict(from_attributes=True)

    ticket_id: UUID
    tenant_id: UUID
    ticket_number: str
    requester_user_id: UUID
    requester_name: str | None = None
    assigned_to_user_id: UUID | None = None
    assignee_name: str | None = None
    category: TicketCategory
    priority: TicketPriority
    status: TicketStatus
    subject: str
    source: TicketSource
    sla_due_at: datetime
    sla_reminder_at: datetime
    sla_escalated_at: datetime | None = None
    is_sla_breached: bool
    public_message_count: int = Field(..., ge=0)
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    satisfaction_rating: int | None = None
    created_at: datetime
    updated_at: datetime


class TicketAssignmentRuleUpsertRequest(BaseModel):
    """Request payload to configure per-category ticket routing and SLA windows."""

    category: TicketCategory
    default_assignee_user_id: UUID | None = None
    default_assignee_role: AssigneeRole = "hr_head"
    sla_hours: int = Field(default=24, ge=1, le=720)
    reminder_before_hours: int = Field(default=4, ge=0, le=719)
    reopen_window_days: int = Field(default=7, ge=0, le=90)

    @model_validator(mode="after")
    def _validate_reminder_before_sla(self) -> "TicketAssignmentRuleUpsertRequest":
        if self.reminder_before_hours >= self.sla_hours:
            raise ValueError("reminder_before_hours must be strictly less than sla_hours.")
        return self


class TicketAssignmentRuleResponse(BaseModel):
    """Serialized per-category ticket assignment and SLA rule."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    category: TicketCategory
    default_assignee_user_id: UUID | None = None
    default_assignee_role: AssigneeRole
    sla_hours: int
    reminder_before_hours: int
    reopen_window_days: int
    created_at: datetime
    updated_at: datetime


class TicketRuleUpdateRequest(BaseModel):
    """Request payload for `PUT /api/v1/tickets/rules/{category}`."""

    default_assignee_user_id: UUID | None = None
    default_assignee_role: AssigneeRole = "hr_head"
    sla_hours: int = Field(default=24, ge=1, le=720)
    reminder_before_hours: int = Field(default=4, ge=0, le=719)
    reopen_window_days: int = Field(default=7, ge=0, le=90)

    @model_validator(mode="after")
    def _validate_reminder_before_sla(self) -> "TicketRuleUpdateRequest":
        if self.reminder_before_hours >= self.sla_hours:
            raise ValueError("reminder_before_hours must be strictly less than sla_hours.")
        return self


class HelpdeskAskRequest(BaseModel):
    """Request payload for `POST /api/v1/helpdesk/ask`."""

    question: str = Field(..., min_length=2, max_length=2000)
    start_workflow: bool = True
    reminder_delay_seconds: float | None = Field(default=None, gt=0)
    breach_delay_seconds: float | None = Field(default=None, gt=0)


class HelpdeskAskResponse(BaseModel):
    """Response envelope emitted by the Helpdesk read-only KB & ticket routing agent."""

    answered: bool
    answer: str
    language: ChatLanguage
    citations: list[CitationItem] = Field(default_factory=list)
    ticket: TicketResponse | None = None
    ticket_number: str | None = None
    category: TicketCategory | None = None
    priority: TicketPriority | None = None
    privileged_request_blocked: bool = False
