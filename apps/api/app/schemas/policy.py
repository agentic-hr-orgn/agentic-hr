"""
Pydantic Schemas for Stage 4 & Stage 5 Policy Document Lifecycle, Preview, Edit & Publish APIs.
"""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.policy_lifecycle import compute_display_status


class PolicyDocumentResponse(BaseModel):
    """Serialized policy document metadata response (backward-compatible with Stage 4)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    title: str
    doc_type: str = "company_policy"
    visibility: str = "all"
    version: int = 1
    file_name: str
    mime_type: str
    size_bytes: int
    storage_provider: str
    storage_key: str
    ingestion_status: str
    lifecycle_status: str = "draft"
    display_status: str = "DRAFT"
    effective_date: date | None = None
    source: str = "upload"
    supersedes_document_id: UUID | None = None
    approval_id: UUID | None = None
    page_count: int = 0
    progress_pct: int = 0
    progress_stage: str | None = None
    error_message: str | None = None
    low_confidence_pages: list[dict[str, Any]] = Field(default_factory=list)
    has_prompt_injection_warning: bool = False
    uploaded_by: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _populate_display_status(self) -> "PolicyDocumentResponse":
        self.display_status = compute_display_status(
            self.ingestion_status,
            self.lifecycle_status,
        )
        return self


class PolicyPagePreviewItem(BaseModel):
    """Per-page extraction preview and OCR quality state."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    page_number: int
    cleaned_text: str
    language: str
    quality_score: float
    needs_ocr: bool
    ocr_applied: bool
    extraction_status: str
    error_message: str | None = None


class PolicyChunkPreviewItem(BaseModel):
    """Chunk preview item inside policy preview response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chunk_index: int
    section_path: str
    page_start: int
    page_end: int
    page_range: str
    text: str
    token_count: int
    language: str
    visibility: str
    has_injection_flag: bool


class PolicyPreviewResponse(BaseModel):
    """Full policy document preview including pages and chunks."""

    document: PolicyDocumentResponse
    pages: list[PolicyPagePreviewItem]
    chunks: list[PolicyChunkPreviewItem]


class PolicyChunkUpdateRequest(BaseModel):
    """Request payload to edit a draft policy chunk's text or section_path."""

    text: str = Field(..., min_length=1, max_length=20000)
    section_path: str | None = Field(default=None, min_length=1, max_length=500)
    mark_reviewed: bool = False


class PolicyPageFixRequest(BaseModel):
    """Request payload to manually supply or repair cleaned text for a needs_ocr page."""

    cleaned_text: str = Field(..., min_length=1, max_length=100000)


class PolicyPublishRequest(BaseModel):
    """Optional parameters when submitting an indexed draft policy for `policy.publish` approval."""

    approver_role: Literal["owner", "hr_head"] = "owner"


class PolicyPublishResponse(BaseModel):
    """Response returned when submitting a policy for `policy.publish` approval."""

    document: PolicyDocumentResponse
    approval_id: UUID
    approval_status: str


class PolicyReviewQueueItem(BaseModel):
    """Summary item in the HR low-confidence / failed ingestion review queue."""

    document: PolicyDocumentResponse
    low_confidence_count: int


class PolicyArchiveResponse(BaseModel):
    """Response returned on soft-delete (`lifecycle_status='archived'`)."""

    status: str
    id: str
