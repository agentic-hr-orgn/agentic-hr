"""
Helpdesk Read-Only KB & Ticket Routing Agent for Agentic HR Core (Stage 7 Prompt 2).

Complies with ADR 002 (Forced RLS), ADR 007 (Model Routing), ADR 010 (Helpdesk Read-Only
Boundary & Code-Level Citation Verification), Rule 3 (LLM proposes, code decides), and
Rule 6 (Untrusted Data).
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.model_gateway import ModelRoute, model_gateway
from app.core.security import AuthenticatedUserContext
from app.core.untrusted import wrap_untrusted_text
from app.schemas.chat import ChatLanguage, CitationItem
from app.schemas.policy_search import PolicySearchHit, PolicySearchRequest
from app.schemas.ticket import (
    HelpdeskAskRequest,
    HelpdeskAskResponse,
    TicketCategory,
    TicketCreateRequest,
    TicketPriority,
)
from app.services.policy_search_service import search_policy
from app.services.text_normalizer import detect_language, normalize_for_search
from app.services.ticket_service import TicketService

_ROMAN_URDU_MARKERS: frozenset[str] = frozenset(
    {
        "kya",
        "kaise",
        "kaisay",
        "mera",
        "meri",
        "mere",
        "mujhe",
        "chutti",
        "chhutti",
        "chahiye",
        "chahie",
        "karna",
        "karni",
        "karo",
        "karein",
        "batao",
        "bataen",
        "batayen",
        "kitni",
        "kitne",
        "hogi",
        "hoga",
        "hain",
        "nahi",
        "nahin",
        "bhai",
        "dost",
        "yaar",
        "kardo",
        "krdo",
        "kiya",
    }
)

_INFORMATIONAL_HOWTO_RE = re.compile(
    r"^\s*(?:how\s+(?:do|can|should)\s+i|where\s+(?:do|can)\s+i|"
    r"what\s+is\s+the\s+(?:process|way|procedure)\s+to|steps\s+to|guide\s+(?:to|for))\b",
    re.IGNORECASE,
)

_SELF_SERVICE_RESET_PATTERN = re.compile(
    r"\b(?:reset|change|recover|unlock|override)\b.{0,40}\b(?:password|passcode|credentials?|account|mfa|2fa)\b",
    re.IGNORECASE,
)

_PRIVILEGED_ACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:make\s+me|grant\s+me|promote\s+me|elevate|give\s+me)\b.{0,30}\b(?:admin|owner|hr\s*head|superuser|root)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:change|update|set|switch|upgrade|elevate|promote)\b.{0,30}\b(?:role|permission|permissions|access\s+level)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:approve|reject|cancel)\b.{0,30}\b(?:leave|request|approval|timesheet)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:edit|update|change|increase|decrease|run|unlock)\b.{0,30}\b(?:payroll|salary|compensation|month\s+lock|attendance\s+lock)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:delete|terminate|remove)\b.{0,30}\b(?:employee|user|record|audit)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:پاسورڈ\s*ری\s*سیٹ|ایڈمن\s*بنا|چھٹی\s*منظور|تنخواہ\s*تبدیل|"
        r"\bpassword\s*(?:reset|badal|change)\b|\badmin\s*banao\b|\bleave\s*approve\b)",
        re.IGNORECASE,
    ),
)

_STOPWORD_NOISE: frozenset[str] = frozenset(
    {
        "how",
        "what",
        "when",
        "where",
        "who",
        "why",
        "the",
        "and",
        "for",
        "with",
        "can",
        "you",
        "please",
        "does",
        "work",
        "from",
        "this",
        "that",
        "mera",
        "meri",
        "kya",
        "hai",
        "hain",
        "kaise",
    }
)


class _HelpdeskSynthesisDraft(BaseModel):
    """Structured LLM synthesis payload verified against retrieved chunks in Python."""

    answerable: bool = True
    answer: str = Field(..., min_length=1)
    cited_chunk_ids: list[str] = Field(default_factory=list)


def detect_chat_language(text: str) -> ChatLanguage:
    """Detects `en`, `ur`, `roman_ur`, or `mixed` deterministically."""
    base = detect_language(text)
    if base in ("ur", "mixed"):
        return base
    tokens = set(re.findall(r"[a-z]+", (text or "").lower()))
    roman_hits = tokens & _ROMAN_URDU_MARKERS
    if len(roman_hits) >= 2 or (len(roman_hits) == 1 and len(tokens) <= 8):
        return "roman_ur"
    return "en"


def is_privileged_action_request(question: str) -> bool:
    """Returns True if the prompt asks the Helpdesk Agent to perform a privileged action."""
    clean = (question or "").strip()
    if any(pat.search(clean) for pat in _PRIVILEGED_ACTION_PATTERNS):
        return True
    return bool(
        _SELF_SERVICE_RESET_PATTERN.search(clean) and not _INFORMATIONAL_HOWTO_RE.search(clean)
    )


def classify_ticket_routing(
    question: str,
    *,
    is_privileged: bool = False,
) -> tuple[TicketCategory, TicketPriority]:
    """Deterministically classifies `(category, priority)` from the user's question."""
    q = (question or "").lower()

    if any(
        kw in q
        for kw in (
            "password",
            "vpn",
            "laptop",
            "email",
            "gmail",
            "slack",
            "access",
            "login",
            "account",
            "mfa",
            "2fa",
            "software",
            "admin",
            "پاسورڈ",
        )
    ):
        category: TicketCategory = "it_access"
    elif any(
        kw in q
        for kw in (
            "salary",
            "payroll",
            "payslip",
            "tax",
            "eobi",
            "bank",
            "iban",
            "reimbursement",
            "تنخواہ",
        )
    ):
        category = "payroll"
    elif any(
        kw in q
        for kw in (
            "leave",
            "attendance",
            "check-in",
            "checkin",
            "holiday",
            "absent",
            "chutti",
            "چھٹی",
            "حاضری",
        )
    ):
        category = "leave_attendance"
    elif any(
        kw in q
        for kw in (
            "desk",
            "chair",
            "ac",
            "air condition",
            "parking",
            "badge",
            "office",
            "wifi",
            "printer",
            "room",
            "facility",
        )
    ):
        category = "facilities"
    elif any(
        kw in q for kw in ("policy", "probation", "notice", "benefits", "insurance", "پالیسی")
    ):
        category = "hr_policy"
    else:
        category = "general"

    if is_privileged or any(
        kw in q for kw in ("locked out", "cannot login", "urgent", "immediately", "emergency")
    ):
        priority: TicketPriority = "high"
    else:
        priority = "medium"

    return category, priority


def _filter_relevant_kb_hits(question: str, hits: list[PolicySearchHit]) -> list[PolicySearchHit]:
    """
    Requires real lexical/semantic grounding so unrelated questions do not force false KB hits
    when deterministic fake embeddings are active.
    """
    q_tokens = {
        tok
        for tok in re.findall(r"[\u0600-\u06FFa-z0-9]+", normalize_for_search(question))
        if len(tok) >= 3 and tok not in _STOPWORD_NOISE
    }
    relevant: list[PolicySearchHit] = []
    for hit in hits:
        if q_tokens:
            hit_tokens = set(
                re.findall(
                    r"[\u0600-\u06FFa-z0-9]+",
                    normalize_for_search(
                        f"{hit.citation.title} {hit.citation.section_path} {hit.text}"
                    ),
                )
            )
            token_overlap = len(q_tokens & hit_tokens)
            if token_overlap >= 1 or (hit.fts_rank is not None and hit.score >= 0.35):
                relevant.append(hit)
        elif hit.score >= 0.55:
            relevant.append(hit)
    return relevant


def verify_citations_against_hits(
    cited_chunk_ids: list[str],
    hits_by_chunk_id: dict[UUID, PolicySearchHit],
) -> list[CitationItem]:
    """
    Code-level citation verification (ADR 010):
    Every cited chunk_id must exist in the current turn's RLS + visibility-filtered
    `helpdesk_kb` retrieval hits, and metadata is built strictly from authoritative DB hits.
    """
    verified: list[CitationItem] = []
    seen: set[UUID] = set()
    for raw_id in cited_chunk_ids:
        try:
            cid = UUID(str(raw_id).strip())
        except ValueError:
            continue
        if cid in seen or cid not in hits_by_chunk_id:
            continue
        hit = hits_by_chunk_id[cid]
        seen.add(cid)
        verified.append(
            CitationItem(
                chunk_id=hit.citation.chunk_id,
                document_id=hit.citation.document_id,
                title=hit.citation.title,
                version=hit.citation.version,
                section=hit.citation.section_path,
                page=hit.citation.page_start,
                snippet=hit.snippet,
            )
        )
    return verified


class HelpdeskAgentService:
    """Read-only Helpdesk KB assistant with automatic ticket routing and escalation."""

    @staticmethod
    async def ask(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        payload: HelpdeskAskRequest,
    ) -> HelpdeskAskResponse:
        question = payload.question.strip()
        language = detect_chat_language(question)
        privileged = is_privileged_action_request(question)

        # 1. Strict Read-Only Boundary: NEVER execute privileged actions; create ticket directly.
        if privileged:
            category, priority = classify_ticket_routing(question, is_privileged=True)
            ticket = await TicketService.create_ticket(
                db,
                actor=actor,
                payload=TicketCreateRequest(
                    category=category,
                    priority=priority,
                    subject=question[:200],
                    description=(
                        f"Helpdesk read-only guardrail escalated a privileged request:\n\n{question}"
                    ),
                    source="helpdesk_agent",
                    start_workflow=payload.start_workflow,
                    reminder_delay_seconds=payload.reminder_delay_seconds,
                    breach_delay_seconds=payload.breach_delay_seconds,
                ),
            )
            return HelpdeskAskResponse(
                answered=False,
                answer=(
                    "I cannot perform privileged account, role, payroll, or approval actions "
                    f"directly. I have created ticket {ticket.ticket_number} "
                    f"(category: {ticket.category}, priority: {ticket.priority}) for the responsible team."
                ),
                language=language,
                citations=[],
                ticket=ticket,
                ticket_number=ticket.ticket_number,
                category=ticket.category,
                priority=ticket.priority,
                privileged_request_blocked=True,
            )

        # 2. Search ONLY published `doc_scope = 'helpdesk_kb'` docs matching caller's visibility
        search_res = await search_policy(
            db,
            actor,
            PolicySearchRequest(
                query=question,
                top_k=5,
                doc_scope="helpdesk_kb",
            ),
        )
        relevant_hits = _filter_relevant_kb_hits(question, search_res.hits)

        if relevant_hits:
            hits_by_id = {h.citation.chunk_id: h for h in relevant_hits}
            top_hits = relevant_hits[:3]

            if settings.USE_FAKE_INTEGRATIONS:
                lead = top_hits[0]
                answer_text = (
                    f"Based on {lead.citation.title} ({lead.citation.section_path or 'General'}): "
                    f"{lead.snippet}"
                )
                cited_ids = [str(h.citation.chunk_id) for h in top_hits]
                answerable = True
            else:
                context_blocks = "\n\n".join(
                    wrap_untrusted_text(
                        f"chunk_id={h.citation.chunk_id}\n"
                        f"title={h.citation.title} (v{h.citation.version})\n"
                        f"section={h.citation.section_path}\n"
                        f"content={h.text}",
                        label=f"helpdesk_kb_chunk_{idx}",
                    )
                    for idx, h in enumerate(top_hits, start=1)
                )
                draft_resp = await model_gateway.structured_output(
                    schema=_HelpdeskSynthesisDraft,
                    route=ModelRoute.STRONG,
                    system_instruction=(
                        "You are the read-only Helpdesk KB assistant. Answer ONLY using facts in the "
                        "provided helpdesk_kb chunks, respond in the user's language, include exact "
                        "chunk_id UUIDs in cited_chunk_ids, and set answerable=false if insufficient. "
                        "Never claim to execute password resets or administrative changes."
                    ),
                    prompt=(
                        f"DETECTED_LANGUAGE: {language}\n"
                        f"{wrap_untrusted_text(question, label='employee_question')}\n\n"
                        f"RETRIEVED_KB_CHUNKS:\n{context_blocks}"
                    ),
                )
                draft: _HelpdeskSynthesisDraft = draft_resp.content  # type: ignore[assignment]
                answerable = bool(draft.answerable)
                answer_text = draft.answer.strip()
                cited_ids = list(draft.cited_chunk_ids)

            if answerable:
                verified_citations = verify_citations_against_hits(cited_ids, hits_by_id)
                if verified_citations:
                    return HelpdeskAskResponse(
                        answered=True,
                        answer=answer_text,
                        language=language,
                        citations=verified_citations,
                        ticket=None,
                        ticket_number=None,
                        category=None,
                        priority=None,
                        privileged_request_blocked=False,
                    )

        # 3. Unanswerable or failed citation check -> classify & create Helpdesk ticket
        category, priority = classify_ticket_routing(question, is_privileged=False)
        ticket = await TicketService.create_ticket(
            db,
            actor=actor,
            payload=TicketCreateRequest(
                category=category,
                priority=priority,
                subject=question[:200],
                description=(
                    f"Auto-escalated from Helpdesk Agent (no matching Helpdesk KB answer):\n\n"
                    f"{question}"
                ),
                source="helpdesk_agent",
                start_workflow=payload.start_workflow,
                reminder_delay_seconds=payload.reminder_delay_seconds,
                breach_delay_seconds=payload.breach_delay_seconds,
            ),
        )
        return HelpdeskAskResponse(
            answered=False,
            answer=(
                f"I could not find a verified answer in our Helpdesk Knowledge Base. "
                f"I have created ticket {ticket.ticket_number} "
                f"(category: {ticket.category}, priority: {ticket.priority}) for support."
            ),
            language=language,
            citations=[],
            ticket=ticket,
            ticket_number=ticket.ticket_number,
            category=ticket.category,
            priority=ticket.priority,
            privileged_request_blocked=False,
        )
