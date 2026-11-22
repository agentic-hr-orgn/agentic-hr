"""
Leave Intake Conversational Agent for Agentic HR Core (Stage 6 Prompt 3).

Complies with Rule 3 (LLM proposes, code decides) and Rule 6 (Untrusted Data):
- Wraps raw employee natural-language input with `wrap_untrusted_text(...)` and extracts
  structured slots via `ModelGateway.structured_output(LeaveIntakeExtraction, ..., route=ModelRoute.CHEAP)`.
- Asks at most ONE focused clarifying question whenever leave type, dates, or half-day period
  is missing or ambiguous.
- Once complete, evaluates deterministic `check_leave(...)` against live PostgreSQL balances,
  holidays, work_week, overlapping leave requests, and department team coverage to produce a
  structured `LeaveConfirmCard`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.model_gateway import ModelRoute, model_gateway
from app.core.security import AuthenticatedUserContext
from app.core.timezones import to_karachi
from app.core.untrusted import wrap_untrusted_text
from app.models.leave import LeaveType
from app.schemas.leave import (
    LeaveConfirmCard,
    LeaveIntakeExtraction,
    LeaveIntakeResponse,
)
from app.services.leave_config_service import LeaveConfigService
from app.services.leave_service import (
    evaluate_leave_policy_from_db,
    resolve_target_employee,
)


def _safe_parse_iso_date(raw: Any) -> date | None:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            return None
    return None


async def parse_leave_intake(
    db: AsyncSession,
    actor: AuthenticatedUserContext,
    message: str,
    conversation_context: dict[str, Any] | None = None,
    employee_id: UUID | None = None,
) -> LeaveIntakeResponse:
    """
    Parses a natural-language leave request (English, Urdu, or Roman Urdu), merges with
    prior `conversation_context`, returns a single clarifying question if incomplete, or
    runs `check_leave(...)` against live DB state to build a `LeaveConfirmCard`.
    """
    t_id = UUID(str(actor.tenant_id))
    ctx = dict(conversation_context or {})

    as_of_date = _safe_parse_iso_date(ctx.get("as_of_date")) or to_karachi(datetime.now(UTC)).date()

    emp = await resolve_target_employee(db, actor, employee_id=employee_id)
    await LeaveConfigService.ensure_default_leave_types(db, t_id, actor_id=actor.user_id)
    active_types = list(
        (
            await db.execute(
                select(LeaveType)
                .where(LeaveType.tenant_id == t_id, LeaveType.is_active.is_(True))
                .order_by(LeaveType.code.asc())
            )
        )
        .scalars()
        .all()
    )
    types_by_code = {lt.code.lower(): lt for lt in active_types}
    available_codes = ", ".join(types_by_code.keys())

    wrapped_msg = wrap_untrusted_text(message, label="employee_leave_message")
    prompt = (
        f"AS_OF_DATE: {as_of_date.isoformat()}\n"
        f"AVAILABLE_LEAVE_TYPES: {available_codes}\n"
        f"PRIOR_CONTEXT: {ctx}\n\n"
        f"{wrapped_msg}"
    )

    model_resp = await model_gateway.structured_output(
        schema=LeaveIntakeExtraction,
        prompt=prompt,
        route=ModelRoute.CHEAP,
        system_instruction=(
            "You extract structured leave request fields (leave_type_code, start_date, end_date, "
            "is_half_day, half_day_period, reason) from an employee's English, Urdu, or Roman-Urdu "
            "message. Never invent dates or leave types that are not stated. Return null for missing fields."
        ),
    )
    extracted_obj: LeaveIntakeExtraction = model_resp.content  # type: ignore[assignment]

    merged_code = (
        extracted_obj.leave_type_code.strip().lower()
        if extracted_obj.leave_type_code and extracted_obj.leave_type_code.strip()
        else (
            str(ctx.get("leave_type_code")).strip().lower() if ctx.get("leave_type_code") else None
        )
    )
    if merged_code and merged_code not in types_by_code:
        merged_code = None

    merged_start = _safe_parse_iso_date(extracted_obj.start_date) or _safe_parse_iso_date(
        ctx.get("start_date")
    )
    merged_end = _safe_parse_iso_date(extracted_obj.end_date) or _safe_parse_iso_date(
        ctx.get("end_date")
    )
    if merged_start is not None and merged_end is None:
        merged_end = merged_start

    merged_is_half = bool(extracted_obj.is_half_day or ctx.get("is_half_day", False))
    merged_period = extracted_obj.half_day_period or ctx.get("half_day_period")
    if merged_period not in ("morning", "afternoon"):
        merged_period = None

    merged_reason = (
        extracted_obj.reason.strip()
        if extracted_obj.reason and extracted_obj.reason.strip()
        else (str(ctx.get("reason")).strip() if ctx.get("reason") else None)
    )
    raw_doc_id = ctx.get("document_id")
    merged_doc_id = UUID(str(raw_doc_id)) if raw_doc_id else None

    extracted_state: dict[str, Any] = {
        "leave_type_code": merged_code,
        "start_date": merged_start.isoformat() if merged_start else None,
        "end_date": merged_end.isoformat() if merged_end else None,
        "is_half_day": merged_is_half,
        "half_day_period": merged_period,
        "reason": merged_reason,
        "document_id": str(merged_doc_id) if merged_doc_id else None,
        "as_of_date": as_of_date.isoformat(),
    }

    if not merged_code:
        return LeaveIntakeResponse(
            status="needs_clarification",
            clarifying_question=(
                f"Which leave type would you like to request ({available_codes})?"
            ),
            extracted=extracted_state,
            confirm_card=None,
        )

    if merged_start is None or merged_end is None:
        return LeaveIntakeResponse(
            status="needs_clarification",
            clarifying_question=(
                "Which start and end dates would you like to request leave for "
                "(e.g., 2026-06-10 to 2026-06-12)?"
            ),
            extracted=extracted_state,
            confirm_card=None,
        )

    if merged_is_half and merged_period is None:
        return LeaveIntakeResponse(
            status="needs_clarification",
            clarifying_question="Would you like the morning or afternoon half-day?",
            extracted=extracted_state,
            confirm_card=None,
        )

    final_reason = merged_reason or message.strip()[:300] or f"{merged_code.title()} leave request"
    extracted_state["reason"] = final_reason

    lt = types_by_code[merged_code]
    policy_res, balance_before = await evaluate_leave_policy_from_db(
        db=db,
        tenant_id=t_id,
        employee=emp,
        leave_type=lt,
        start_date=merged_start,
        end_date=merged_end,
        as_of_date=as_of_date,
        is_half_day=merged_is_half,
        half_day_period=merged_period,
        document_id=merged_doc_id,
        actor_id=actor.user_id,
    )

    balance_after = (balance_before - policy_res.working_days).quantize(Decimal("0.01"))
    if balance_after < Decimal("0.00") and lt.is_paid:
        balance_after = Decimal("0.00")

    card = LeaveConfirmCard(
        employee_id=emp.id,
        employee_name=emp.full_name,
        leave_type_id=lt.id,
        leave_type_code=lt.code,
        leave_type_name=lt.name,
        start_date=merged_start,
        end_date=merged_end,
        is_half_day=merged_is_half,
        half_day_period=merged_period,
        working_days=policy_res.working_days,
        reason=final_reason,
        document_id=merged_doc_id,
        balance_before=balance_before,
        balance_after=balance_after,
        policy_check_status=policy_res.status,
        policy_check_reasons=policy_res.reasons,
    )
    return LeaveIntakeResponse(
        status="ready_to_confirm",
        clarifying_question=None,
        extracted=extracted_state,
        confirm_card=card,
    )
