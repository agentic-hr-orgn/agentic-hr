"""
Six Server-Session-Bound Policy Brain Tools for Agentic HR Core (Stage 7 Prompt 3).

Complies with ADR 002 (Forced RLS), ADR 008 (Stage 5 Hybrid Search), ADR 009 (Stage 6 Leave),
and ADR 010 (Server-Session-Bound Tool Security):
- Every tool is bound exclusively to a server-verified `RequestContext` (`tenant_id`, `user_id`,
  `employee_id`, `role`, `is_hr_head`) derived from Clerk auth + PostgreSQL membership.
- ZERO tool methods accept `user_id`, `employee_id`, or `tenant_id` parameters from LLM arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_args
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUserContext
from app.models.employee import Employee
from app.models.tenant import set_tenant_context
from app.schemas.policy_search import PolicySearchRequest
from app.schemas.ticket import TicketCategory, TicketCreateRequest, TicketPriority, TicketSource
from app.services.leave_intake_agent import parse_leave_intake
from app.services.leave_service import LeaveService
from app.services.policy_search_service import search_policy as stage5_search_policy
from app.services.ticket_service import TicketService

_VALID_TICKET_CATEGORIES: frozenset[str] = frozenset(get_args(TicketCategory))
_VALID_TICKET_PRIORITIES: frozenset[str] = frozenset(get_args(TicketPriority))
_VALID_TICKET_SOURCES: frozenset[str] = frozenset(get_args(TicketSource))


@dataclass(frozen=True)
class RequestContext:
    """Server-verified session context binding every Policy Brain tool call."""

    tenant_id: UUID
    user_id: UUID
    employee_id: UUID | None
    role: str
    is_hr_head: bool
    actor: AuthenticatedUserContext


async def build_request_context(
    db: AsyncSession,
    actor: AuthenticatedUserContext,
) -> RequestContext:
    """Resolves `RequestContext` purely from server-verified auth + tenant Employee link."""
    t_id = UUID(str(actor.tenant_id))
    u_id = UUID(str(actor.user_id))

    emp_id = (
        await db.execute(
            select(Employee.id).where(
                Employee.tenant_id == t_id,
                Employee.user_id == u_id,
            )
        )
    ).scalar_one_or_none()
    if emp_id is None and actor.email:
        emp_id = (
            await db.execute(
                select(Employee.id).where(
                    Employee.tenant_id == t_id,
                    func.lower(Employee.email) == actor.email.strip().lower(),
                )
            )
        ).scalar_one_or_none()

    return RequestContext(
        tenant_id=t_id,
        user_id=u_id,
        employee_id=emp_id,
        role=actor.role,
        is_hr_head=bool(actor.is_hr_head),
        actor=actor,
    )


class PolicyBrainToolkit:
    """
    Per-request toolkit exposing the exact 6 ADR-010 server-session-bound tools:
    1. `search_policy`
    2. `get_my_leave_balance`
    3. `get_my_leave_requests`
    4. `get_my_manager`
    5. `create_ticket`
    6. `start_leave_request`
    """

    def __init__(
        self,
        db: AsyncSession,
        ctx: RequestContext,
        *,
        start_ticket_workflow: bool = True,
    ) -> None:
        self.db = db
        self.ctx = ctx
        self.start_ticket_workflow = start_ticket_workflow

    def _can_view_hr_only(self) -> bool:
        return bool(self.ctx.role == "owner" or self.ctx.is_hr_head)

    async def search_policy(self, query: str, top_k: int = 6) -> list[dict[str, Any]]:
        """
        Searches ONLY published (`active` + `indexed`) `hr_policy` documents inside `ctx.tenant_id`.
        Strictly excludes `visibility == 'hr_only'` unless caller is Owner or HR Head.
        """
        clean_query = (query or "").strip()
        if len(clean_query) < 2:
            return []

        safe_top_k = max(1, min(20, int(top_k)))
        search_res = await stage5_search_policy(
            self.db,
            self.ctx.actor,
            PolicySearchRequest(
                query=clean_query,
                top_k=safe_top_k,
                doc_scope="hr_policy",
            ),
        )

        allow_hr_only = self._can_view_hr_only()
        results: list[dict[str, Any]] = []
        for hit in search_res.hits:
            if hit.citation.visibility == "hr_only" and not allow_hr_only:
                continue
            results.append(
                {
                    "chunk_id": str(hit.citation.chunk_id),
                    "document_id": str(hit.citation.document_id),
                    "title": hit.citation.title,
                    "version": int(hit.citation.version),
                    "doc_type": hit.citation.doc_type,
                    "visibility": hit.citation.visibility,
                    "section": hit.citation.section_path,
                    "page": hit.citation.page_start,
                    "page_range": hit.citation.page_range,
                    "language": hit.citation.language,
                    "snippet": hit.snippet,
                    "content": hit.text,
                    "score": float(hit.score),
                    "rrf_score": float(hit.rrf_score),
                    "fts_rank": hit.fts_rank,
                    "trgm_rank": hit.trgm_rank,
                    "has_injection_flag": bool(hit.has_injection_flag),
                }
            )
        return results

    async def get_my_leave_balance(self, year: int | None = None) -> dict[str, Any]:
        """Queries Stage 6 leave balances strictly for the authenticated caller."""
        try:
            items = await LeaveService.get_balances(
                self.db,
                actor=self.ctx.actor,
                year=year,
                self_only=True,
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                return {"linked_employee": False, "balances": [], "detail": str(exc.detail)}
            raise
        return {
            "linked_employee": True,
            "balances": [item.model_dump(mode="json") for item in items],
        }

    async def get_my_leave_requests(self, limit: int = 10) -> dict[str, Any]:
        """Queries Stage 6 leave requests strictly for the authenticated caller."""
        safe_limit = max(1, min(50, int(limit)))
        try:
            items = await LeaveService.list_my_requests(self.db, actor=self.ctx.actor)
        except HTTPException as exc:
            if exc.status_code == 404:
                return {"linked_employee": False, "requests": [], "detail": str(exc.detail)}
            raise
        sliced = items[:safe_limit]
        return {
            "linked_employee": True,
            "requests": [item.model_dump(mode="json") for item in sliced],
        }

    async def get_my_manager(self) -> dict[str, Any]:
        """Resolves the authenticated employee's direct manager from Stage 4 `employees`."""
        if self.ctx.employee_id is None:
            return {
                "linked_employee": False,
                "has_manager": False,
                "manager": None,
            }

        emp = (
            await self.db.execute(
                select(Employee).where(
                    Employee.tenant_id == self.ctx.tenant_id,
                    Employee.id == self.ctx.employee_id,
                )
            )
        ).scalar_one_or_none()
        if emp is None or emp.manager_id is None:
            return {
                "linked_employee": emp is not None,
                "has_manager": False,
                "manager": None,
            }

        mgr = (
            await self.db.execute(
                select(Employee).where(
                    Employee.tenant_id == self.ctx.tenant_id,
                    Employee.id == emp.manager_id,
                )
            )
        ).scalar_one_or_none()
        if mgr is None:
            return {
                "linked_employee": True,
                "has_manager": False,
                "manager": None,
            }

        return {
            "linked_employee": True,
            "has_manager": True,
            "manager": {
                "employee_id": str(mgr.id),
                "full_name": mgr.full_name,
                "job_title": mgr.job_title,
                "email": mgr.email,
            },
        }

    async def create_ticket(
        self,
        subject: str,
        description: str,
        category: str = "hr_policy",
        priority: str = "medium",
        *,
        source: str = "chat_ask_hr",
        source_session_id: UUID | None = None,
    ) -> dict[str, Any]:
        """
        Delegates to Prompt 2's `TicketService.create_ticket(...)` for the authenticated user,
        then immediately restores PostgreSQL Forced-RLS transaction context (`set_tenant_context`).
        """
        clean_cat: TicketCategory = (
            category if category in _VALID_TICKET_CATEGORIES else "hr_policy"  # type: ignore[assignment]
        )
        clean_pri: TicketPriority = (
            priority if priority in _VALID_TICKET_PRIORITIES else "medium"  # type: ignore[assignment]
        )
        clean_src: TicketSource = (
            source if source in _VALID_TICKET_SOURCES else "chat_ask_hr"  # type: ignore[assignment]
        )
        safe_subject = (subject or "Policy Brain HR Inquiry").strip()[:255]
        if len(safe_subject) < 3:
            safe_subject = "Policy Brain HR Inquiry"
        safe_desc = (description or safe_subject).strip()[:10000]
        if len(safe_desc) < 3:
            safe_desc = safe_subject

        ticket = await TicketService.create_ticket(
            self.db,
            actor=self.ctx.actor,
            payload=TicketCreateRequest(
                category=clean_cat,
                priority=clean_pri,
                subject=safe_subject,
                description=safe_desc,
                source=clean_src,
                source_session_id=source_session_id,
                start_workflow=self.start_ticket_workflow,
            ),
        )
        # Re-establish transaction-local Forced RLS after TicketService.create_ticket commits
        await set_tenant_context(
            self.db,
            str(self.ctx.tenant_id),
            self.ctx.actor.clerk_user_id,
        )
        return ticket.model_dump(mode="json")

    async def start_leave_request(self, natural_language_request: str) -> dict[str, Any]:
        """
        Delegates to Stage 6 `parse_leave_intake(...)` to produce a structured leave intake
        handoff payload (dates, type, working days, balance check) without bypassing Stage 6.
        """
        try:
            intake = await parse_leave_intake(
                db=self.db,
                actor=self.ctx.actor,
                message=natural_language_request,
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                return {
                    "status": "no_employee_profile",
                    "clarifying_question": None,
                    "extracted": {},
                    "confirm_card": None,
                    "prefill_url": "/leave",
                    "detail": str(exc.detail),
                }
            raise

        payload = intake.model_dump(mode="json")
        payload["prefill_url"] = "/leave"
        return payload
