"""
Pydantic Schemas for Stage 7 Policy Brain Chat, Grounded Citations & Answer Feedback.
Complies with ADR 010 (Code-Level Citation Verification & Bilingual Session Memory).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.stage7_common import (
    ChatLanguage,
    CitationItem,
    TicketCategory,
    TicketPriority,
)

ChatRole = Literal["user", "assistant", "system"]
FeedbackRating = Literal["up", "down"]


class ToolCallTrace(BaseModel):
    """Auditable trace of a server-session-bound Policy Brain tool invocation."""

    tool_name: str = Field(..., min_length=1, max_length=64)
    status: Literal["ok", "error", "denied"] = "ok"
    summary: str | None = None


class LeaveHandoffPayload(BaseModel):
    """Structured draft leave parameters handed off from chat to Stage 6 Leave intake."""

    leave_type_code: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_half_day: bool = False
    half_day_period: Literal["morning", "afternoon"] | None = None
    reason: str | None = None


class PolicyBrainAnswer(BaseModel):
    """Structured assistant response emitted by the Policy Brain & Helpdesk pipelines."""

    answer: str = Field(..., min_length=1)
    language: ChatLanguage = "en"
    intent: str = Field(default="policy_qa", min_length=1, max_length=64)
    citations: list[CitationItem] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    abstained: bool = False
    ticket_id: UUID | None = None
    ticket_number: str | None = None
    handoff_payload: dict[str, Any] | LeaveHandoffPayload | None = None
    topic_tag: str | None = None


class ChatSessionCreateRequest(BaseModel):
    """Request payload to open a new bilingual Policy Brain chat session."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    language: ChatLanguage = "en"


class ChatSessionResponse(BaseModel):
    """Serialized Policy Brain chat session."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    title: str
    language: ChatLanguage
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChatAskRequest(BaseModel):
    """Request payload to submit a user turn to Policy Brain or Helpdesk agent."""

    session_id: UUID | None = None
    message: str = Field(..., min_length=1, max_length=4000)
    language: ChatLanguage | None = None
    scope: Literal["hr_policy", "helpdesk_kb", "all"] = "all"


class ChatMessageResponse(BaseModel):
    """Serialized persisted turn in `chat_messages`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    session_id: UUID
    role: ChatRole
    content: str
    language: ChatLanguage
    intent: str | None = None
    citations: list[CitationItem] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    abstained: bool = False
    ticket_id: UUID | None = None
    ticket_number: str | None = None
    handoff_payload: dict[str, Any] | None = None
    topic_tag: str | None = None
    latency_ms: int | None = None
    model_tier: str | None = None
    token_usage: dict[str, Any] | None = None
    cost_usd: Decimal | None = None
    created_at: datetime


class AnswerFeedbackRequest(BaseModel):
    """Request payload for per-message thumbs up/down feedback."""

    rating: FeedbackRating
    comment: str | None = Field(default=None, max_length=2000)
    topic_tag: str | None = Field(default=None, max_length=120)


class AnswerFeedbackResponse(BaseModel):
    """Serialized feedback record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    message_id: UUID
    user_id: UUID
    rating: FeedbackRating
    comment: str | None = None
    topic_tag: str | None = None
    created_at: datetime


class ChatTurnRequest(BaseModel):
    """Request payload for submitting a turn to an existing chat session."""

    message: str = Field(..., min_length=1, max_length=4000)
    language: ChatLanguage | None = None
    start_workflow: bool = True


class ChatTurnResponse(BaseModel):
    """Synchronous Policy Brain response pairing persisted user + assistant turns."""

    session_id: UUID
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse


class AskHRRequest(BaseModel):
    """Request payload for escalating an assistant reply via the Ask HR action."""

    category: TicketCategory | None = None
    priority: TicketPriority = "medium"
    note: str | None = Field(default=None, max_length=2000)
    start_workflow: bool = True


class AskHRResponse(BaseModel):
    """Response returned when an Ask HR ticket is created or reused for a message."""

    ticket_id: UUID
    ticket_number: str
    category: TicketCategory
    priority: TicketPriority
    message_id: UUID


class PolicyGapGroup(BaseModel):
    """Aggregated Policy Brain knowledge gap grouped by `topic_tag`."""

    topic_tag: str
    total_count: int = Field(..., ge=0)
    abstained_count: int = Field(..., ge=0)
    downvoted_count: int = Field(..., ge=0)
    sample_questions: list[str] = Field(default_factory=list)
    ticket_numbers: list[str] = Field(default_factory=list)
    latest_at: datetime


class PolicyGapsResponse(BaseModel):
    """HR Head / Owner dashboard payload summarizing policy knowledge gaps."""

    total_gaps: int = Field(..., ge=0)
    groups: list[PolicyGapGroup] = Field(default_factory=list)
