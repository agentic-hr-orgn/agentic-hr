"""
Stage 7 Prompt 4: 18-Question Policy Brain & Helpdesk Evaluation Runner.

Supports two execution modes:
- `--mode fake`: Deterministic CI mode (`USE_FAKE_INTEGRATIONS=true`), zero external API calls.
- `--mode real`: Live LLM + embedding evaluation (`USE_FAKE_INTEGRATIONS=false`), fail-fast if
  required live model API keys are missing.

Provisions and resets an isolated `org_stage7_eval_sandbox` tenant cleanly via
`MIGRATION_DATABASE_URL` (preserving immutable `tenants`/`audit_logs` records) and runs all queries
through the production Forced-RLS runtime (`DATABASE_URL`).

Usage:
    uv run --package agentic-hr-api python -m app.evals.run_stage7_eval \
        --mode fake --output docs/stage-7-eval-results.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import AuthenticatedUserContext
from app.core.timezones import to_karachi
from app.models.approval import Approval
from app.models.chat import AnswerFeedback, ChatMessage, ChatSession
from app.models.connector import SentMessage
from app.models.department import Department
from app.models.employee import Employee
from app.models.leave import (
    LeaveBalance,
    LeaveLedger,
    LeaveRequest,
    LeaveType,
    WorkWeek,
)
from app.models.membership import Membership
from app.models.notification import Notification
from app.models.outbox import OutboxEvent
from app.models.policy_chunk import PolicyChunk
from app.models.policy_chunk_embedding import PolicyChunkEmbedding
from app.models.policy_document import PolicyDocument
from app.models.policy_document_page import PolicyDocumentPage
from app.models.role import Role
from app.models.tenant import set_tenant_context
from app.models.tenant_entity import Tenant
from app.models.ticket import Ticket, TicketAssignmentRule, TicketMessage
from app.models.user import User
from app.schemas.chat import ChatSessionCreateRequest, ChatTurnRequest
from app.schemas.ticket import HelpdeskAskRequest
from app.services.chat_service import ChatService
from app.services.helpdesk_agent_service import HelpdeskAgentService
from app.services.leave_config_service import (
    LeaveConfigService,
    initialize_employee_leave_balances,
)
from app.services.policy_ingestion_service import run_policy_ingestion_inline
from app.services.policy_service import PolicyService
from app.services.storage_helper import StorageHelper

EVAL_CLERK_ORG_ID = "org_stage7_eval_sandbox"
EVAL_TENANT_NAME = "Stage 7 Eval Sandbox"
EVAL_USERS_SPEC = (
    {
        "key": "owner",
        "clerk_user_id": "user_stage7_eval_owner",
        "email": "eval.owner@eval-sandbox.local",
        "name": "Eval Owner",
        "role": "owner",
        "is_hr_head": True,
        "code": "EVL-001",
        "job_title": "Chief Executive Officer",
    },
    {
        "key": "manager",
        "clerk_user_id": "user_stage7_eval_manager",
        "email": "eval.manager@eval-sandbox.local",
        "name": "Eval Manager",
        "role": "manager",
        "is_hr_head": True,
        "code": "EVL-002",
        "job_title": "Engineering Lead",
    },
    {
        "key": "employee",
        "clerk_user_id": "user_stage7_eval_employee",
        "email": "eval.employee@eval-sandbox.local",
        "name": "Eval Employee",
        "role": "employee",
        "is_hr_head": False,
        "code": "EVL-102",
        "job_title": "Software Engineer",
    },
)


@dataclass
class EvalSandboxActors:
    tenant_id: UUID
    clerk_org_id: str
    actors: dict[str, AuthenticatedUserContext]
    employee_ids: dict[str, UUID]


def _default_dataset_path() -> Path:
    return Path(__file__).with_name("stage7_eval_18.json")


def _validate_real_mode_keys() -> None:
    has_chat_key = any(
        bool(v and str(v).strip())
        for v in (
            settings.STRONG_MODEL_API_KEY,
            settings.CHEAP_MODEL_API_KEY,
            settings.GOOGLE_API_KEY,
            settings.GROQ_API_KEY,
            settings.OPENROUTER_API_KEY,
        )
    )
    has_embed_key = bool(
        (settings.EMBEDDING_MODEL_API_KEY and settings.EMBEDDING_MODEL_API_KEY.strip())
        or (settings.GOOGLE_API_KEY and settings.GOOGLE_API_KEY.strip())
    )
    if not (has_chat_key and has_embed_key):
        raise RuntimeError(
            "Stage 7 --mode real requires configured live LLM and embedding API keys "
            "(e.g. GOOGLE_API_KEY / STRONG_MODEL_API_KEY / CHEAP_MODEL_API_KEY / "
            "EMBEDDING_MODEL_API_KEY)."
        )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, math.ceil((pct / 100.0) * len(ordered)) - 1)
    return round(ordered[min(idx, len(ordered) - 1)], 2)


async def _cleanup_eval_tenant_mutable_data(
    migration_db: AsyncSession,
    tenant_id: UUID,
) -> None:
    """
    Deletes all mutable domain records belonging to the isolated eval tenant in FK-safe order.
    Never deletes `tenants` or `audit_logs` (preserving `trg_audit_logs_immutable`).
    """
    storage_keys = list(
        (
            await migration_db.execute(
                select(PolicyDocument.storage_key).where(PolicyDocument.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )

    for model in (
        AnswerFeedback,
        ChatMessage,
        ChatSession,
        TicketMessage,
        Ticket,
        TicketAssignmentRule,
        Notification,
        OutboxEvent,
        SentMessage,
        PolicyChunkEmbedding,
        PolicyChunk,
        PolicyDocumentPage,
        PolicyDocument,
        Approval,
        LeaveRequest,
        LeaveLedger,
        LeaveBalance,
        LeaveType,
        WorkWeek,
        Employee,
        Department,
    ):
        await migration_db.execute(delete(model).where(model.tenant_id == tenant_id))

    await migration_db.commit()

    for key in storage_keys:
        if key:
            StorageHelper.delete_local_file(tenant_id=str(tenant_id), storage_key=key)


async def _provision_eval_sandbox(
    migration_db: AsyncSession,
    corpus: list[dict[str, Any]],
) -> EvalSandboxActors:
    await migration_db.execute(
        pg_insert(Tenant)
        .values(
            clerk_org_id=EVAL_CLERK_ORG_ID,
            name=EVAL_TENANT_NAME,
            status="active",
        )
        .on_conflict_do_nothing(index_elements=["clerk_org_id"])
    )
    tenant_id = (
        await migration_db.execute(
            select(Tenant.id).where(Tenant.clerk_org_id == EVAL_CLERK_ORG_ID)
        )
    ).scalar_one()

    await _cleanup_eval_tenant_mutable_data(migration_db, tenant_id)

    actors: dict[str, AuthenticatedUserContext] = {}
    user_ids: dict[str, UUID] = {}
    for spec in EVAL_USERS_SPEC:
        await migration_db.execute(
            pg_insert(User)
            .values(
                clerk_user_id=spec["clerk_user_id"],
                email=spec["email"],
                name=spec["name"],
            )
            .on_conflict_do_nothing(index_elements=["clerk_user_id"])
        )
        uid = (
            await migration_db.execute(
                select(User.id).where(User.clerk_user_id == spec["clerk_user_id"])
            )
        ).scalar_one()
        user_ids[spec["key"]] = uid

        await migration_db.execute(
            pg_insert(Role)
            .values(role_key=spec["role"], name=spec["role"].title())
            .on_conflict_do_nothing(index_elements=["role_key"])
        )
        role_id = (
            await migration_db.execute(select(Role.id).where(Role.role_key == spec["role"]))
        ).scalar_one()

        await migration_db.execute(
            pg_insert(Membership)
            .values(
                tenant_id=tenant_id,
                user_id=uid,
                role_id=role_id,
                is_hr_head=spec["is_hr_head"],
                status="active",
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "user_id"])
        )
        actors[spec["key"]] = AuthenticatedUserContext(
            user_id=str(uid),
            clerk_user_id=spec["clerk_user_id"],
            email=spec["email"],
            name=spec["name"],
            tenant_id=str(tenant_id),
            clerk_org_id=EVAL_CLERK_ORG_ID,
            tenant_name=EVAL_TENANT_NAME,
            role=spec["role"],
            is_hr_head=bool(spec["is_hr_head"]),
        )

    dept_id = uuid4()
    migration_db.add(
        Department(
            id=dept_id,
            tenant_id=tenant_id,
            name="Engineering",
            code="ENG",
        )
    )
    await migration_db.flush()

    employee_ids: dict[str, UUID] = {}
    parent_mgr: dict[str, str | None] = {
        "owner": None,
        "manager": "owner",
        "employee": "manager",
    }
    today_utc = datetime.now(UTC).date()
    for spec in EVAL_USERS_SPEC:
        key = spec["key"]
        emp_id = uuid4()
        mgr_key = parent_mgr[key]
        migration_db.add(
            Employee(
                id=emp_id,
                tenant_id=tenant_id,
                user_id=user_ids[key],
                employee_code=spec["code"],
                full_name=spec["name"],
                email=spec["email"],
                department_id=dept_id,
                manager_id=employee_ids[mgr_key] if mgr_key else None,
                job_title=spec["job_title"],
                employment_type="full_time",
                join_date=today_utc,
                status="active",
            )
        )
        employee_ids[key] = emp_id
    await migration_db.flush()

    owner_uid = user_ids["owner"]
    await LeaveConfigService.get_or_create_work_week(migration_db, tenant_id, actor_id=owner_uid)
    await LeaveConfigService.ensure_default_leave_types(migration_db, tenant_id, actor_id=owner_uid)
    await initialize_employee_leave_balances(
        migration_db,
        tenant_id,
        employee_ids["employee"],
        today_utc.year,
        actor_id=owner_uid,
    )
    await migration_db.commit()

    # Upload, ingest, and activate each benchmark corpus document inside the isolated tenant
    owner_actor = actors["owner"]
    for doc_spec in corpus:
        await set_tenant_context(migration_db, str(tenant_id), owner_actor.clerk_user_id)
        uploaded = await PolicyService.upload_policy(
            migration_db,
            current_user=owner_actor,
            file_name=f"{doc_spec['key']}.txt",
            content=str(doc_spec["content"]).encode("utf-8"),
            content_type="text/plain",
            title=doc_spec["title"],
            doc_type=doc_spec.get("doc_type", "company_policy"),
            visibility=doc_spec.get("visibility", "all"),
            language=doc_spec.get("language", "en"),
            storage_provider="local",
        )
        doc_row = (
            await migration_db.execute(
                select(PolicyDocument).where(PolicyDocument.id == uploaded.id)
            )
        ).scalar_one()
        if doc_row.ingestion_status != "indexed":
            await run_policy_ingestion_inline(migration_db, tenant_id, doc_row.id)
            await set_tenant_context(migration_db, str(tenant_id), owner_actor.clerk_user_id)
            doc_row = (
                await migration_db.execute(
                    select(PolicyDocument).where(PolicyDocument.id == uploaded.id)
                )
            ).scalar_one()

        doc_row.lifecycle_status = "active"
        await migration_db.commit()

    return EvalSandboxActors(
        tenant_id=tenant_id,
        clerk_org_id=EVAL_CLERK_ORG_ID,
        actors=actors,
        employee_ids=employee_ids,
    )


async def _verify_citations_against_db(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    citations: list[dict[str, Any]],
    can_view_hr_only: bool,
) -> tuple[bool, str | None]:
    if not citations:
        return True, None

    chunk_ids: list[UUID] = []
    for item in citations:
        try:
            chunk_ids.append(UUID(str(item["chunk_id"])))
        except (KeyError, ValueError):
            return False, "Malformed citation chunk_id"

    rows = (
        await db.execute(
            select(
                PolicyChunk.id,
                PolicyChunk.document_id,
                PolicyDocument.visibility,
                PolicyDocument.lifecycle_status,
            )
            .join(PolicyDocument, PolicyDocument.id == PolicyChunk.document_id)
            .where(
                PolicyChunk.tenant_id == tenant_id,
                PolicyChunk.id.in_(chunk_ids),
            )
        )
    ).all()
    by_id = {row[0]: row for row in rows}
    for item in citations:
        cid = UUID(str(item["chunk_id"]))
        row = by_id.get(cid)
        if row is None:
            return False, f"Citation chunk '{cid}' missing in DB"
        if str(row[1]) != str(item.get("document_id")):
            return False, f"Citation document_id mismatch for '{cid}'"
        if row[2] == "hr_only" and not can_view_hr_only:
            return False, f"Citation chunk '{cid}' leaked hr_only document"
        if row[3] != "active":
            return False, f"Citation chunk '{cid}' points to non-active document"
    return True, None


def _evaluate_case_assertions(
    case: dict[str, Any],
    *,
    answer_text: str,
    abstained: bool,
    citations: list[dict[str, Any]],
    citations_db_ok: bool,
    ticket_id: str | None,
    tool_calls: list[dict[str, Any]],
    handoff_payload: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    expected_abstained = bool(case.get("expected_abstained", False))
    if abstained != expected_abstained:
        failures.append(f"abstained={abstained} (expected {expected_abstained})")

    min_cits = int(case.get("min_citations", 0))
    if len(citations) < min_cits:
        failures.append(f"citations={len(citations)} (< {min_cits})")
    if not citations_db_ok:
        failures.append("citation DB/RBAC verification failed")

    if case.get("expected_ticket_created") is True and not ticket_id:
        failures.append("expected ticket_id to be created")
    if case.get("expected_ticket_created") is False and ticket_id is not None:
        failures.append(f"unexpected ticket created ({ticket_id})")

    if case.get("expected_tool"):
        expected_tool = str(case["expected_tool"])
        names = [str(t.get("tool_name") or "") for t in tool_calls]
        if expected_tool not in names:
            failures.append(f"missing tool_call '{expected_tool}' (got {names})")

    if case.get("expected_handoff") and not handoff_payload:
        failures.append("missing handoff_payload")

    ans_lower = (answer_text or "").lower()
    any_subs = list(case.get("expected_any_substrings") or [])
    if any_subs and not any(str(s).lower() in ans_lower for s in any_subs):
        failures.append(f"answer missing any expected keyword from {any_subs}")

    for bad in case.get("forbidden_substrings") or []:
        if str(bad).lower() in ans_lower:
            failures.append(f"answer contained forbidden substring '{bad}'")

    return (len(failures) == 0), failures


def _render_markdown_report(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# Stage 7 Policy Brain & Helpdesk Evaluation Report",
        "",
        f"- **Mode**: `{report['mode']}`",
        f"- **Timestamp (PKT)**: `{report['timestamp_pkt']}`",
        (
            f"- **Models**: `cheap={report['models']['cheap']}`, "
            f"`strong={report['models']['strong']}`, `embedding={report['models']['embedding']}`"
        ),
        "",
        "## Summary Metrics",
        "",
        "| Metric | Value |",
        "| :--- | :--- |",
        f"| Questions Evaluated | {s['passed']} / {s['total']} ({s['accuracy_pct']:.1f}%) |",
        f"| Citation Verification Pass Rate | {s['citation_verified']} / {s['total']} ({s['citation_verification_pct']:.1f}%) |",
        f"| Latency (avg / p50 / p95 ms) | {s['avg_latency_ms']:.1f} / {s['p50_latency_ms']:.1f} / {s['p95_latency_ms']:.1f} ms |",
        f"| Tokens (prompt / completion / total) | {s['prompt_tokens']} / {s['completion_tokens']} / {s['total_tokens']} |",
        f"| Estimated Cost (USD) | ${s['total_cost_usd']:.6f} |",
        "",
        "## Per-Question Results",
        "",
        "| ID | Category | Endpoint | Pass | Citations OK | Abstained | Latency (ms) | Tokens | Notes |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | :--- |",
    ]
    for r in report["results"]:
        pass_badge = "PASS" if r["passed"] else "FAIL"
        cit_badge = "PASS" if r["citations_ok"] else "FAIL"
        notes = "; ".join(r["failures"]) if r["failures"] else "OK"
        lines.append(
            f"| {r['id']} | {r['category']} | {r['endpoint']} | {pass_badge} | "
            f"{cit_badge} | {r['abstained']} | {r['latency_ms']:.1f} | {r['total_tokens']} | {notes} |"
        )
    lines.append("")
    return "\n".join(lines)


async def run_stage7_eval(
    *,
    mode: Literal["fake", "real"] = "fake",
    output_path: Path | str | None = None,
    dataset_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Runs the 18-question Stage 7 Policy Brain + Helpdesk evaluation benchmark and returns
    structured summary metrics and per-question outcomes.
    """
    if mode == "real":
        _validate_real_mode_keys()

    ds_path = Path(dataset_path) if dataset_path else _default_dataset_path()
    dataset = json.loads(ds_path.read_text(encoding="utf-8"))
    corpus: list[dict[str, Any]] = list(dataset["corpus"])
    cases: list[dict[str, Any]] = list(dataset["cases"])

    orig_fake = settings.USE_FAKE_INTEGRATIONS
    orig_inline = settings.INGEST_INLINE
    settings.USE_FAKE_INTEGRATIONS = mode == "fake"
    settings.INGEST_INLINE = True

    migration_engine = create_async_engine(
        settings.MIGRATION_DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )
    app_engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        echo=False,
    )
    mig_factory = async_sessionmaker(migration_engine, class_=AsyncSession, expire_on_commit=False)
    app_factory = async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False)

    sandbox: EvalSandboxActors | None = None
    results: list[dict[str, Any]] = []

    try:
        async with mig_factory() as mig_db:
            sandbox = await _provision_eval_sandbox(mig_db, corpus)

        sessions_by_group: dict[str, UUID] = {}

        async with app_factory() as app_db:
            for case in cases:
                role_key = str(case.get("actor_role") or "employee")
                actor = sandbox.actors[role_key]
                can_hr = actor.role == "owner" or actor.is_hr_head
                endpoint = str(case.get("endpoint") or "chat")
                t0 = time.perf_counter()

                if endpoint == "helpdesk":
                    await set_tenant_context(app_db, str(sandbox.tenant_id), actor.clerk_user_id)
                    hd_resp = await HelpdeskAgentService.ask(
                        app_db,
                        actor=actor,
                        payload=HelpdeskAskRequest(
                            question=case["question"],
                            start_workflow=False,
                        ),
                    )
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    cits = [c.model_dump(mode="json") for c in hd_resp.citations]
                    abstained = not hd_resp.answered
                    answer_text = hd_resp.answer
                    ticket_id = str(hd_resp.ticket.id) if hd_resp.ticket else None
                    tool_calls: list[dict[str, Any]] = []
                    handoff_payload = None
                    prompt_tokens = 0
                    completion_tokens = 0
                    total_tokens = 0
                    cost_usd = 0.0
                else:
                    sg = str(case.get("session_group") or case["id"])
                    if sg not in sessions_by_group:
                        await set_tenant_context(
                            app_db, str(sandbox.tenant_id), actor.clerk_user_id
                        )
                        sess = await ChatService.create_session(
                            app_db,
                            actor=actor,
                            payload=ChatSessionCreateRequest(title=f"Eval {sg}"),
                        )
                        sessions_by_group[sg] = sess.id

                    await set_tenant_context(app_db, str(sandbox.tenant_id), actor.clerk_user_id)
                    turn = await ChatService.send_message(
                        app_db,
                        actor=actor,
                        session_id=sessions_by_group[sg],
                        payload=ChatTurnRequest(
                            message=case["question"],
                            language=None,
                            start_workflow=False,
                        ),
                    )
                    asst = turn.assistant_message
                    elapsed_ms = float(
                        asst.latency_ms
                        if asst.latency_ms is not None
                        else (time.perf_counter() - t0) * 1000.0
                    )
                    cits = [c.model_dump(mode="json") for c in asst.citations]
                    abstained = bool(asst.abstained)
                    answer_text = asst.content
                    ticket_id = str(asst.ticket_id) if asst.ticket_id else None
                    tool_calls = [t.model_dump(mode="json") for t in asst.tool_calls]
                    handoff_payload = asst.handoff_payload
                    token_usage = asst.token_usage or {}
                    prompt_tokens = int(token_usage.get("prompt_tokens") or 0)
                    completion_tokens = int(token_usage.get("completion_tokens") or 0)
                    total_tokens = int(token_usage.get("total_tokens") or 0)
                    cost_usd = float(asst.cost_usd or 0.0)

                await set_tenant_context(app_db, str(sandbox.tenant_id), actor.clerk_user_id)
                cit_db_ok, cit_err = await _verify_citations_against_db(
                    app_db,
                    tenant_id=sandbox.tenant_id,
                    citations=cits,
                    can_view_hr_only=can_hr,
                )
                passed, failures = _evaluate_case_assertions(
                    case,
                    answer_text=answer_text,
                    abstained=abstained,
                    citations=cits,
                    citations_db_ok=cit_db_ok,
                    ticket_id=ticket_id,
                    tool_calls=tool_calls,
                    handoff_payload=handoff_payload,
                )
                if cit_err and not cit_db_ok and cit_err not in failures:
                    failures.append(cit_err)

                citations_ok = cit_db_ok and (len(cits) >= int(case.get("min_citations", 0)))
                results.append(
                    {
                        "id": case["id"],
                        "category": case["category"],
                        "endpoint": endpoint,
                        "passed": passed,
                        "citations_ok": citations_ok,
                        "abstained": abstained,
                        "latency_ms": round(elapsed_ms, 2),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "cost_usd": round(cost_usd, 6),
                        "failures": failures,
                    }
                )
    finally:
        settings.USE_FAKE_INTEGRATIONS = orig_fake
        settings.INGEST_INLINE = orig_inline
        if sandbox is not None:
            async with mig_factory() as cleanup_db:
                await _cleanup_eval_tenant_mutable_data(cleanup_db, sandbox.tenant_id)
        await app_engine.dispose()
        await migration_engine.dispose()

    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    cit_ok_count = sum(1 for r in results if r["citations_ok"])
    latencies = [float(r["latency_ms"]) for r in results]
    avg_latency = round(sum(latencies) / total, 2) if total else 0.0

    report = {
        "mode": mode,
        "timestamp_pkt": to_karachi(datetime.now(UTC)).isoformat(),
        "models": {
            "cheap": settings.CHEAP_MODEL_ID or settings.CHEAP_MODEL,
            "strong": settings.STRONG_MODEL_ID or settings.STRONG_MODEL,
            "embedding": settings.EMBEDDING_MODEL,
        },
        "summary": {
            "total": total,
            "passed": passed_count,
            "accuracy_pct": round((passed_count / total) * 100.0, 2) if total else 0.0,
            "citation_verified": cit_ok_count,
            "citation_verification_pct": (
                round((cit_ok_count / total) * 100.0, 2) if total else 0.0
            ),
            "avg_latency_ms": avg_latency,
            "p50_latency_ms": _percentile(latencies, 50.0),
            "p95_latency_ms": _percentile(latencies, 95.0),
            "prompt_tokens": sum(int(r["prompt_tokens"]) for r in results),
            "completion_tokens": sum(int(r["completion_tokens"]) for r in results),
            "total_tokens": sum(int(r["total_tokens"]) for r in results),
            "total_cost_usd": round(sum(float(r["cost_usd"]) for r in results), 6),
        },
        "results": results,
    }

    if output_path is not None:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(_render_markdown_report(report), encoding="utf-8")

    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage 7 Policy Brain & Helpdesk Eval Runner")
    parser.add_argument(
        "--mode",
        choices=["fake", "real"],
        default="fake",
        help="Execution mode: deterministic fake or live model benchmark.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional Markdown report path (e.g. docs/stage-7-eval-results.md).",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="Optional path to benchmark JSON dataset.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = asyncio.run(
        run_stage7_eval(
            mode=args.mode,
            output_path=args.output,
            dataset_path=args.dataset,
        )
    )
    print(_render_markdown_report(report))


if __name__ == "__main__":
    main()
