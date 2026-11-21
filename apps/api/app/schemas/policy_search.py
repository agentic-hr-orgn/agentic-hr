"""
Canonical Stage 7 Citation & Stage 5 Hybrid Bilingual Policy Search Schemas.
Retrieval-only scope in Stage 5 (ADR 008): no `/ask` LLM synthesis until Stage 7.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PolicyCitation(BaseModel):
    """Canonical Stage 7 citation payload identifying the exact source clause and page range."""

    chunk_id: UUID
    document_id: UUID
    title: str
    version: int
    doc_type: str
    visibility: str
    section_path: str
    page_start: int
    page_end: int
    page_range: str
    language: str


class PolicySearchRequest(BaseModel):
    """Hybrid bilingual retrieval query payload."""

    query: str = Field(..., min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)
    doc_type: Literal["company_policy", "labour_law", "helpdesk_knowledge"] | None = None
    language: Literal["en", "ur", "mixed"] | None = None
    document_id: UUID | None = None
    effective_as_of: date | None = None


class PolicySearchHit(BaseModel):
    """Single ranked retrieval hit with Stage 7 citation and fusion diagnostics."""

    citation: PolicyCitation
    text: str
    snippet: str
    score: float
    rrf_score: float
    vector_rank: int | None = None
    fts_rank: int | None = None
    trgm_rank: int | None = None
    has_injection_flag: bool = False


class PolicySearchResponse(BaseModel):
    """Hybrid bilingual search response envelope."""

    query: str
    normalized_query: str
    query_language: Literal["en", "ur", "mixed"]
    total_candidates: int
    hits: list[PolicySearchHit]
    embedding_model: str
    embedding_dim: int
