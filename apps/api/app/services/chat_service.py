"""
Policy Brain Chat Session, SSE Streaming, Feedback, Ask HR & Policy Gaps Service (Stage 7 Prompt 3).

Complies with ADR 002 (Forced RLS), ADR 010 (Bilingual session memory, Ask HR escalation,
HR Policy Gaps aggregation), and Pass 2 / Refinement 1-3 DB lifecycle invariants:
- Re-establishes `set_tenant_context` immediately after any `TicketService.create_ticket` commit.
- SSE `/stream` uses a dedicated `AsyncSessionLocal()` with `set_tenant_context` inside the generator
  and streams `token` chunks ONLY after code-level `verify` / `respond` succeeds.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUserContext
from app.db.session import AsyncSessionLocal
from app.models.chat import AnswerFeedback, ChatMessage, ChatSession
from app.models.tenant import set_tenant_context
from app.models.ticket import Ticket
from app.schemas.chat import (
    AnswerFeedbackRequest,
    AnswerFeedbackResponse,
    AskHRRequest,
    AskHRResponse,
    ChatMessageResponse,
    ChatSessionCreateRequest,
    ChatSessionResponse,
    ChatTurnRequest,
    ChatTurnResponse,
    CitationItem,
    PolicyBrainAnswer,
    PolicyGapGroup,
    PolicyGapsResponse,
    ToolCallTrace,
)
from app.schemas.ticket import TicketCategory, TicketCreateRequest
from app.services.helpdesk_agent_service import classify_ticket_routing, detect_chat_language
from app.services.policy_brain_graph import (
    AnswerSynthesizerFn,
    PolicyBrainState,
    build_policy_brain_graph,
    run_policy_brain_turn,
)
from app.services.policy_brain_tools import PolicyBrainToolkit, build_request_context
from app.services.ticket_service import TicketService

logger = logging.getLogger("agentic_hr.services.chat")


def _serialize_chat_message(
    msg: ChatMessage,
    *,
    ticket_number: str | None = None,
) -> ChatMessageResponse:
    cits = [CitationItem.model_validate(c) for c in (msg.citations or [])]
    tools = [ToolCallTrace.model_validate(t) for t in (msg.tool_calls or [])]
    return ChatMessageResponse(
        id=msg.id,
        tenant_id=msg.tenant_id,
        session_id=msg.session_id,
        role=msg.role,  # type: ignore[arg-type]
        content=msg.content,
        language=msg.language,  # type: ignore[arg-type]
        intent=msg.intent,
        citations=cits,
        tool_calls=tools,
        abstained=bool(msg.abstained),
        ticket_id=msg.ticket_id,
        ticket_number=ticket_number,
        handoff_payload=msg.handoff_payload,
        topic_tag=msg.topic_tag,
        latency_ms=msg.latency_ms,
        model_tier=msg.model_tier,
        token_usage=msg.token_usage,
        cost_usd=msg.cost_usd,
        created_at=msg.created_at,
    )


def _format_sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _chunk_text_for_sse(text: str, chunk_size: int = 40) -> list[str]:
    clean = text or ""
    if not clean:
        return []
    words = clean.split(" ")
    chunks: list[str] = []
    buf: list[str] = []
    curr_len = 0
    for idx, word in enumerate(words):
        piece = word if idx == len(words) - 1 else f"{word} "
        if buf and curr_len + len(piece) > chunk_size:
            chunks.append("".join(buf))
            buf = [piece]
            curr_len = len(piece)
        else:
            buf.append(piece)
            curr_len += len(piece)
    if buf:
        chunks.append("".join(buf))
    return chunks


class ChatService:
    """Service layer managing Policy Brain sessions, LangGraph turns, SSE, feedback & policy gaps."""

    @staticmethod
    async def create_session(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        payload: ChatSessionCreateRequest,
    ) -> ChatSessionResponse:
        t_id = UUID(str(actor.tenant_id))
        u_id = UUID(str(actor.user_id))
        sess = ChatSession(
            tenant_id=t_id,
            user_id=u_id,
            title=(payload.title or "New conversation").strip()[:255] or "New conversation",
            language=payload.language,
        )
        db.add(sess)
        await db.flush()
        resp = ChatSessionResponse.model_validate(sess)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)
        return resp

    @staticmethod
    async def list_sessions(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
    ) -> list[ChatSessionResponse]:
        t_id = UUID(str(actor.tenant_id))
        u_id = UUID(str(actor.user_id))
        rows = (
            (
                await db.execute(
                    select(ChatSession)
                    .where(ChatSession.tenant_id == t_id, ChatSession.user_id == u_id)
                    .order_by(
                        ChatSession.last_message_at.desc().nullslast(),
                        ChatSession.created_at.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        return [ChatSessionResponse.model_validate(r) for r in rows]

    @staticmethod
    async def get_owned_session(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        session_id: UUID,
    ) -> ChatSession:
        t_id = UUID(str(actor.tenant_id))
        u_id = UUID(str(actor.user_id))
        sess = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.tenant_id == t_id,
                    ChatSession.id == session_id,
                )
            )
        ).scalar_one_or_none()
        if sess is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chat session '{session_id}' not found.",
            )
        if sess.user_id != u_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: you may only access your own chat sessions.",
            )
        return sess

    @staticmethod
    async def get_session_messages(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        session_id: UUID,
    ) -> list[ChatMessageResponse]:
        sess = await ChatService.get_owned_session(db, actor=actor, session_id=session_id)
        rows = (
            await db.execute(
                select(ChatMessage, Ticket.ticket_number)
                .outerjoin(
                    Ticket,
                    and_(
                        ChatMessage.ticket_id == Ticket.id,
                        ChatMessage.tenant_id == Ticket.tenant_id,
                    ),
                )
                .where(
                    ChatMessage.tenant_id == sess.tenant_id,
                    ChatMessage.session_id == sess.id,
                )
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
        ).all()
        return [_serialize_chat_message(msg, ticket_number=t_num) for msg, t_num in rows]

    @staticmethod
    async def delete_session(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        session_id: UUID,
    ) -> None:
        sess = await ChatService.get_owned_session(db, actor=actor, session_id=session_id)
        await db.delete(sess)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)

    @staticmethod
    async def _load_recent_history(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        session_id: UUID,
        limit: int = 6,
    ) -> list[dict[str, str]]:
        rows = (
            (
                await db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.tenant_id == tenant_id,
                        ChatMessage.session_id == session_id,
                    )
                    .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [{"role": m.role, "content": m.content} for m in reversed(rows)]

    @staticmethod
    async def send_message(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        session_id: UUID,
        payload: ChatTurnRequest,
        answer_synthesizer: AnswerSynthesizerFn | None = None,
    ) -> ChatTurnResponse:
        t_id = UUID(str(actor.tenant_id))
        sess = await ChatService.get_owned_session(db, actor=actor, session_id=session_id)
        recent_history = await ChatService._load_recent_history(
            db,
            tenant_id=t_id,
            session_id=sess.id,
            limit=6,
        )

        clean_text = payload.message.strip()
        user_lang = payload.language or detect_chat_language(clean_text)

        user_msg = ChatMessage(
            tenant_id=t_id,
            session_id=sess.id,
            role="user",
            content=clean_text,
            language=user_lang,
        )
        db.add(user_msg)
        await db.flush()
        user_msg_resp = _serialize_chat_message(user_msg)
        user_msg_id = user_msg.id
        sess_id = sess.id
        sess_title = sess.title

        ctx = await build_request_context(db, actor)
        toolkit = PolicyBrainToolkit(db, ctx, start_ticket_workflow=payload.start_workflow)
        graph_state = await run_policy_brain_turn(
            toolkit,
            question=clean_text,
            history=recent_history,
            session_id=str(sess_id),
            language=user_lang,
            answer_synthesizer=answer_synthesizer,
        )

        # Ensure Forced RLS context is active even if abstain/create_ticket committed mid-turn
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)

        ans = PolicyBrainAnswer.model_validate(graph_state["final_answer"])
        ticket_uuid = ans.ticket_id or (
            UUID(str(graph_state["ticket_id"])) if graph_state.get("ticket_id") else None
        )
        ticket_num = ans.ticket_number or graph_state.get("ticket_number")
        handoff_dict = (
            ans.handoff_payload.model_dump(mode="json")
            if hasattr(ans.handoff_payload, "model_dump")
            else ans.handoff_payload
        )
        token_usage = {
            "prompt_tokens": int(graph_state.get("prompt_tokens") or 0),
            "completion_tokens": int(graph_state.get("completion_tokens") or 0),
            "total_tokens": int(graph_state.get("total_tokens") or 0),
        }
        raw_cost = graph_state.get("cost_usd")
        cost_dec = Decimal(str(raw_cost)) if raw_cost is not None else None

        # Stamp user message topic_tag too if needed, and insert assistant turn
        persisted_user = (
            await db.execute(
                select(ChatMessage).where(
                    ChatMessage.tenant_id == t_id,
                    ChatMessage.id == user_msg_id,
                )
            )
        ).scalar_one()
        persisted_user.topic_tag = ans.topic_tag or graph_state.get("topic_tag")

        asst_msg = ChatMessage(
            tenant_id=t_id,
            session_id=sess_id,
            role="assistant",
            content=ans.answer,
            language=ans.language,
            intent=ans.intent,
            citations=[c.model_dump(mode="json") for c in ans.citations],
            tool_calls=[t.model_dump(mode="json") for t in ans.tool_calls],
            abstained=bool(ans.abstained),
            ticket_id=ticket_uuid,
            handoff_payload=handoff_dict,
            topic_tag=ans.topic_tag or graph_state.get("topic_tag"),
            latency_ms=int(graph_state.get("latency_ms") or 1),
            model_tier=str(graph_state.get("model_tier") or "strong"),
            token_usage=token_usage,
            cost_usd=cost_dec,
        )
        db.add(asst_msg)

        live_sess = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.tenant_id == t_id,
                    ChatSession.id == sess_id,
                )
            )
        ).scalar_one()
        live_sess.last_message_at = datetime.now(UTC)
        live_sess.language = ans.language
        if sess_title == "New conversation":
            live_sess.title = clean_text[:80]

        await db.flush()
        user_msg_resp = _serialize_chat_message(persisted_user)
        asst_msg_resp = _serialize_chat_message(asst_msg, ticket_number=ticket_num)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)

        return ChatTurnResponse(
            session_id=sess_id,
            user_message=user_msg_resp,
            assistant_message=asst_msg_resp,
        )

    @staticmethod
    async def stream_message_events(
        *,
        actor: AuthenticatedUserContext,
        session_id: UUID,
        payload: ChatTurnRequest,
    ) -> AsyncIterator[str]:
        """
        Dedicated SSE async generator opening its own `AsyncSessionLocal()` with `set_tenant_context`:
        - Emits `event: status` as each LangGraph node finishes.
        - Emits `event: token` strictly AFTER code-level `verify` / `respond` succeeds.
        - Emits `event: done` with persisted `message_id`, `citations`, `abstained`,
          `ticket_number`, and `handoff_payload`.
        """
        t_id = UUID(str(actor.tenant_id))
        started = time.perf_counter()

        try:
            async with AsyncSessionLocal() as stream_db:
                await set_tenant_context(stream_db, actor.tenant_id, actor.clerk_user_id)
                sess = await ChatService.get_owned_session(
                    stream_db,
                    actor=actor,
                    session_id=session_id,
                )
                recent_history = await ChatService._load_recent_history(
                    stream_db,
                    tenant_id=t_id,
                    session_id=sess.id,
                    limit=6,
                )

                clean_text = payload.message.strip()
                user_lang = payload.language or detect_chat_language(clean_text)

                user_msg = ChatMessage(
                    tenant_id=t_id,
                    session_id=sess.id,
                    role="user",
                    content=clean_text,
                    language=user_lang,
                )
                stream_db.add(user_msg)
                await stream_db.flush()
                user_msg_id = user_msg.id
                sess_title = sess.title

                ctx = await build_request_context(stream_db, actor)
                toolkit = PolicyBrainToolkit(
                    stream_db,
                    ctx,
                    start_ticket_workflow=payload.start_workflow,
                )
                compiled = build_policy_brain_graph(toolkit)
                graph_state: dict[str, Any] = {
                    "question": clean_text,
                    "history": recent_history,
                    "session_id": str(session_id),
                    "language": user_lang,
                    "started_at": started,
                }
                initial_input: PolicyBrainState = {
                    "question": clean_text,
                    "history": recent_history,
                    "session_id": str(session_id),
                    "language": user_lang,
                    "started_at": started,
                }

                async for update in compiled.astream(initial_input, stream_mode="updates"):
                    for node_name, node_delta in update.items():
                        if isinstance(node_delta, dict):
                            graph_state.update(node_delta)
                        yield _format_sse_event("status", {"node": node_name})

                # Re-establish Forced RLS context in case abstain/create_ticket committed
                await set_tenant_context(stream_db, actor.tenant_id, actor.clerk_user_id)

                ans = PolicyBrainAnswer.model_validate(graph_state["final_answer"])
                latency_ms = max(1, round((time.perf_counter() - started) * 1000))
                ticket_uuid = ans.ticket_id or (
                    UUID(str(graph_state["ticket_id"])) if graph_state.get("ticket_id") else None
                )
                ticket_num = ans.ticket_number or graph_state.get("ticket_number")
                handoff_dict = (
                    ans.handoff_payload.model_dump(mode="json")
                    if hasattr(ans.handoff_payload, "model_dump")
                    else ans.handoff_payload
                )
                token_usage = {
                    "prompt_tokens": int(graph_state.get("prompt_tokens") or 0),
                    "completion_tokens": int(graph_state.get("completion_tokens") or 0),
                    "total_tokens": int(graph_state.get("total_tokens") or 0),
                }
                raw_cost = graph_state.get("cost_usd")
                cost_dec = Decimal(str(raw_cost)) if raw_cost is not None else None

                persisted_user = (
                    await stream_db.execute(
                        select(ChatMessage).where(
                            ChatMessage.tenant_id == t_id,
                            ChatMessage.id == user_msg_id,
                        )
                    )
                ).scalar_one()
                persisted_user.topic_tag = ans.topic_tag or graph_state.get("topic_tag")

                asst_msg = ChatMessage(
                    tenant_id=t_id,
                    session_id=session_id,
                    role="assistant",
                    content=ans.answer,
                    language=ans.language,
                    intent=ans.intent,
                    citations=[c.model_dump(mode="json") for c in ans.citations],
                    tool_calls=[t.model_dump(mode="json") for t in ans.tool_calls],
                    abstained=bool(ans.abstained),
                    ticket_id=ticket_uuid,
                    handoff_payload=handoff_dict,
                    topic_tag=ans.topic_tag or graph_state.get("topic_tag"),
                    latency_ms=latency_ms,
                    model_tier=str(graph_state.get("model_tier") or "strong"),
                    token_usage=token_usage,
                    cost_usd=cost_dec,
                )
                stream_db.add(asst_msg)

                live_sess = (
                    await stream_db.execute(
                        select(ChatSession).where(
                            ChatSession.tenant_id == t_id,
                            ChatSession.id == session_id,
                        )
                    )
                ).scalar_one()
                live_sess.last_message_at = datetime.now(UTC)
                live_sess.language = ans.language
                if sess_title == "New conversation":
                    live_sess.title = clean_text[:80]

                await stream_db.flush()
                asst_id = str(asst_msg.id)
                await stream_db.commit()

            # Stream verified tokens ONLY after verification + DB persistence succeeded
            for token_piece in _chunk_text_for_sse(ans.answer):
                yield _format_sse_event("token", {"text": token_piece})

            yield _format_sse_event(
                "done",
                {
                    "message_id": asst_id,
                    "session_id": str(session_id),
                    "answer": ans.answer,
                    "language": ans.language,
                    "intent": ans.intent,
                    "citations": [c.model_dump(mode="json") for c in ans.citations],
                    "tool_calls": [t.model_dump(mode="json") for t in ans.tool_calls],
                    "abstained": bool(ans.abstained),
                    "ticket_id": str(ticket_uuid) if ticket_uuid else None,
                    "ticket_number": ticket_num,
                    "handoff_payload": handoff_dict,
                    "topic_tag": ans.topic_tag or graph_state.get("topic_tag"),
                    "latency_ms": latency_ms,
                },
            )
        except Exception as exc:
            logger.exception("Policy Brain SSE stream error for session %s", session_id)
            yield _format_sse_event("error", {"detail": str(exc)})

    @staticmethod
    async def _get_owned_message(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        message_id: UUID,
    ) -> tuple[ChatMessage, ChatSession]:
        t_id = UUID(str(actor.tenant_id))
        u_id = UUID(str(actor.user_id))
        row = (
            await db.execute(
                select(ChatMessage, ChatSession)
                .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                .where(
                    ChatMessage.tenant_id == t_id,
                    ChatSession.tenant_id == t_id,
                    ChatMessage.id == message_id,
                )
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chat message '{message_id}' not found.",
            )
        msg, sess = row
        if sess.user_id != u_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: you may only interact with your own chat messages.",
            )
        return msg, sess

    @staticmethod
    async def submit_feedback(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        message_id: UUID,
        payload: AnswerFeedbackRequest,
    ) -> AnswerFeedbackResponse:
        t_id = UUID(str(actor.tenant_id))
        u_id = UUID(str(actor.user_id))
        msg, _sess = await ChatService._get_owned_message(db, actor=actor, message_id=message_id)
        topic = payload.topic_tag or msg.topic_tag or "general_hr"

        stmt = (
            pg_insert(AnswerFeedback)
            .values(
                tenant_id=t_id,
                message_id=msg.id,
                user_id=u_id,
                rating=payload.rating,
                comment=payload.comment,
                topic_tag=topic,
            )
            .on_conflict_do_update(
                constraint="uq_answer_feedback_tenant_msg_user",
                set_={
                    "rating": payload.rating,
                    "comment": payload.comment,
                    "topic_tag": topic,
                },
            )
            .returning(AnswerFeedback)
        )
        fb = (await db.execute(stmt)).scalar_one()
        resp = AnswerFeedbackResponse.model_validate(fb)
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)
        return resp

    @staticmethod
    async def ask_hr_for_message(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
        message_id: UUID,
        payload: AskHRRequest,
    ) -> AskHRResponse:
        t_id = UUID(str(actor.tenant_id))
        msg, sess = await ChatService._get_owned_message(db, actor=actor, message_id=message_id)

        if msg.ticket_id is not None:
            existing_tkt = (
                await db.execute(
                    select(Ticket).where(
                        Ticket.tenant_id == t_id,
                        Ticket.id == msg.ticket_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_tkt is not None:
                return AskHRResponse(
                    ticket_id=existing_tkt.id,
                    ticket_number=existing_tkt.ticket_number,
                    category=existing_tkt.category,  # type: ignore[arg-type]
                    priority=existing_tkt.priority,  # type: ignore[arg-type]
                    message_id=msg.id,
                )

        prev_user_q = (
            await db.execute(
                select(ChatMessage.content)
                .where(
                    ChatMessage.tenant_id == t_id,
                    ChatMessage.session_id == sess.id,
                    ChatMessage.role == "user",
                    ChatMessage.created_at <= msg.created_at,
                )
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        question_text = prev_user_q or msg.content
        inferred_cat, inferred_pri = classify_ticket_routing(question_text)
        final_cat: TicketCategory = payload.category or (
            inferred_cat
            if inferred_cat
            in ("hr_policy", "leave_attendance", "payroll", "it_access", "facilities", "general")
            else "hr_policy"
        )
        final_pri = payload.priority or inferred_pri

        desc_lines = [f"Employee Question:\n{question_text}"]
        if payload.note:
            desc_lines.append(f"Additional Employee Note:\n{payload.note.strip()}")
        desc_lines.append(f"Assistant Response Context:\n{msg.content[:800]}")

        ticket = await TicketService.create_ticket(
            db,
            actor=actor,
            payload=TicketCreateRequest(
                category=final_cat,
                priority=final_pri,
                subject=f"Ask HR: {question_text[:180]}",
                description="\n\n".join(desc_lines),
                source="chat_ask_hr",
                source_session_id=sess.id,
                start_workflow=payload.start_workflow,
            ),
        )

        # Re-establish Forced RLS context after TicketService.create_ticket commits
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)
        persisted_msg = (
            await db.execute(
                select(ChatMessage).where(
                    ChatMessage.tenant_id == t_id,
                    ChatMessage.id == msg.id,
                )
            )
        ).scalar_one()
        persisted_msg.ticket_id = ticket.id
        await db.commit()
        await set_tenant_context(db, actor.tenant_id, actor.clerk_user_id)

        return AskHRResponse(
            ticket_id=ticket.id,
            ticket_number=ticket.ticket_number,
            category=ticket.category,
            priority=ticket.priority,
            message_id=msg.id,
        )

    @staticmethod
    async def get_policy_gaps(
        db: AsyncSession,
        *,
        actor: AuthenticatedUserContext,
    ) -> PolicyGapsResponse:
        """
        HR Head / Owner aggregation of Policy Brain knowledge gaps:
        combines abstained assistant messages and down-voted answers grouped by `topic_tag`,
        deduplicating per `message_id` for `total_count` and sampling the preceding user question.
        """
        t_id = UUID(str(actor.tenant_id))

        abstained_rows = (
            await db.execute(
                select(ChatMessage, Ticket.ticket_number)
                .outerjoin(
                    Ticket,
                    and_(
                        ChatMessage.ticket_id == Ticket.id,
                        ChatMessage.tenant_id == Ticket.tenant_id,
                    ),
                )
                .where(
                    ChatMessage.tenant_id == t_id,
                    ChatMessage.role == "assistant",
                    ChatMessage.abstained.is_(True),
                )
                .order_by(desc(ChatMessage.created_at))
            )
        ).all()

        downvoted_rows = (
            await db.execute(
                select(AnswerFeedback, ChatMessage, Ticket.ticket_number)
                .join(ChatMessage, AnswerFeedback.message_id == ChatMessage.id)
                .outerjoin(
                    Ticket,
                    and_(
                        ChatMessage.ticket_id == Ticket.id,
                        ChatMessage.tenant_id == Ticket.tenant_id,
                    ),
                )
                .where(
                    AnswerFeedback.tenant_id == t_id,
                    ChatMessage.tenant_id == t_id,
                    AnswerFeedback.rating == "down",
                )
                .order_by(desc(AnswerFeedback.created_at))
            )
        ).all()

        # Gather preceding user questions for all affected sessions in one query
        session_ids = {m.session_id for m, _ in abstained_rows} | {
            m.session_id for _fb, m, _ in downvoted_rows
        }
        user_turns_by_session: dict[UUID, list[tuple[datetime, str]]] = {}
        if session_ids:
            u_rows = (
                await db.execute(
                    select(ChatMessage.session_id, ChatMessage.created_at, ChatMessage.content)
                    .where(
                        ChatMessage.tenant_id == t_id,
                        ChatMessage.session_id.in_(session_ids),
                        ChatMessage.role == "user",
                    )
                    .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                )
            ).all()
            for s_id, c_at, content in u_rows:
                user_turns_by_session.setdefault(s_id, []).append((c_at, content))

        def _preceding_question(msg: ChatMessage) -> str:
            turns = user_turns_by_session.get(msg.session_id, [])
            candidate: str | None = None
            for u_at, u_content in turns:
                if u_at <= msg.created_at:
                    candidate = u_content
                else:
                    break
            return candidate or msg.content

        # Deduplicate per message_id within each topic_tag
        groups_map: dict[str, dict[str, Any]] = {}
        all_gap_msg_ids: set[UUID] = set()

        def _ensure_group(tag: str, ts: datetime) -> dict[str, Any]:
            if tag not in groups_map:
                groups_map[tag] = {
                    "topic_tag": tag,
                    "msg_ids": set(),
                    "abstained_ids": set(),
                    "downvoted_ids": set(),
                    "sample_questions": [],
                    "ticket_numbers": [],
                    "latest_at": ts,
                }
            elif ts > groups_map[tag]["latest_at"]:
                groups_map[tag]["latest_at"] = ts
            return groups_map[tag]

        for msg, t_num in abstained_rows:
            tag = (msg.topic_tag or "uncategorized").strip() or "uncategorized"
            grp = _ensure_group(tag, msg.created_at)
            grp["msg_ids"].add(msg.id)
            grp["abstained_ids"].add(msg.id)
            all_gap_msg_ids.add(msg.id)
            q_text = _preceding_question(msg)
            if (
                q_text
                and q_text not in grp["sample_questions"]
                and len(grp["sample_questions"]) < 5
            ):
                grp["sample_questions"].append(q_text)
            if t_num and t_num not in grp["ticket_numbers"]:
                grp["ticket_numbers"].append(t_num)

        for fb, msg, t_num in downvoted_rows:
            tag = (msg.topic_tag or fb.topic_tag or "uncategorized").strip() or "uncategorized"
            ts = max(msg.created_at, fb.created_at)
            grp = _ensure_group(tag, ts)
            grp["msg_ids"].add(msg.id)
            grp["downvoted_ids"].add(msg.id)
            all_gap_msg_ids.add(msg.id)
            q_text = _preceding_question(msg)
            if (
                q_text
                and q_text not in grp["sample_questions"]
                and len(grp["sample_questions"]) < 5
            ):
                grp["sample_questions"].append(q_text)
            if t_num and t_num not in grp["ticket_numbers"]:
                grp["ticket_numbers"].append(t_num)

        result_groups = [
            PolicyGapGroup(
                topic_tag=g["topic_tag"],
                total_count=len(g["msg_ids"]),
                abstained_count=len(g["abstained_ids"]),
                downvoted_count=len(g["downvoted_ids"]),
                sample_questions=g["sample_questions"],
                ticket_numbers=g["ticket_numbers"],
                latest_at=g["latest_at"],
            )
            for g in groups_map.values()
        ]
        result_groups.sort(key=lambda g: (-g.total_count, -g.latest_at.timestamp(), g.topic_tag))

        return PolicyGapsResponse(
            total_gaps=len(all_gap_msg_ids),
            groups=result_groups,
        )
