"""
Stage 7 Policy Brain Chat, SSE Streaming, Feedback, Ask HR & Policy Gaps REST API Router.

Mounted at `/api/v1` (via `app.api.v1.api.api_router` in `app.main`):
- POST   /api/v1/chat/sessions
- GET    /api/v1/chat/sessions
- GET    /api/v1/chat/sessions/{session_id}/messages
- DELETE /api/v1/chat/sessions/{session_id}
- POST   /api/v1/chat/sessions/{session_id}/messages
- POST   /api/v1/chat/sessions/{session_id}/stream
- POST   /api/v1/chat/messages/{message_id}/feedback
- POST   /api/v1/chat/messages/{message_id}/ask-hr
- GET    /api/v1/chat/policy-gaps
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import RateLimiter
from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_owner_or_hr_head,
)
from app.db.session import get_db
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
    PolicyGapsResponse,
)
from app.services.chat_service import ChatService

router = APIRouter()


@router.post(
    "/chat/sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["chat"],
)
async def create_chat_session(
    payload: ChatSessionCreateRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatSessionResponse:
    """Creates a new Policy Brain chat session scoped to `current_user.user_id`."""
    return await ChatService.create_session(db, actor=current_user, payload=payload)


@router.get("/chat/sessions", response_model=list[ChatSessionResponse], tags=["chat"])
async def list_chat_sessions(
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatSessionResponse]:
    """Lists chat sessions belonging to `current_user` in the active tenant."""
    return await ChatService.list_sessions(db, actor=current_user)


@router.get(
    "/chat/sessions/{session_id}/messages",
    response_model=list[ChatMessageResponse],
    tags=["chat"],
)
async def list_session_messages(
    session_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatMessageResponse]:
    """Lists chronological messages (with persisted `ticket_number`) for an owned session."""
    return await ChatService.get_session_messages(
        db,
        actor=current_user,
        session_id=session_id,
    )


@router.delete(
    "/chat/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["chat"],
)
async def delete_chat_session(
    session_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Deletes a chat session owned by `current_user`."""
    await ChatService.delete_session(db, actor=current_user, session_id=session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/chat/sessions/{session_id}/messages",
    response_model=ChatTurnResponse,
    tags=["chat"],
    dependencies=[Depends(RateLimiter(requests=30, window_seconds=60, scope="chat.ask"))],
)
async def send_chat_message(
    session_id: UUID,
    payload: ChatTurnRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatTurnResponse:
    """Runs the compiled Policy Brain LangGraph pipeline synchronously and persists both turns."""
    return await ChatService.send_message(
        db,
        actor=current_user,
        session_id=session_id,
        payload=payload,
    )


@router.post(
    "/chat/sessions/{session_id}/stream",
    tags=["chat"],
    dependencies=[Depends(RateLimiter(requests=30, window_seconds=60, scope="chat.stream"))],
)
async def stream_chat_message(
    session_id: UUID,
    payload: ChatTurnRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    Validates auth + session ownership synchronously, then streams Policy Brain LangGraph
    progress (`status`) and verified answer tokens (`token` + `done`) using a dedicated
    `AsyncSessionLocal()` inside the async generator.
    """
    await ChatService.get_owned_session(db, actor=current_user, session_id=session_id)
    return StreamingResponse(
        ChatService.stream_message_events(
            actor=current_user,
            session_id=session_id,
            payload=payload,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/chat/messages/{message_id}/feedback",
    response_model=AnswerFeedbackResponse,
    tags=["chat"],
)
async def submit_answer_feedback(
    message_id: UUID,
    payload: AnswerFeedbackRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnswerFeedbackResponse:
    """Upserts thumbs up/down feedback on a message owned by `current_user`."""
    return await ChatService.submit_feedback(
        db,
        actor=current_user,
        message_id=message_id,
        payload=payload,
    )


@router.post(
    "/chat/messages/{message_id}/ask-hr",
    response_model=AskHRResponse,
    tags=["chat"],
)
async def ask_hr_from_message(
    message_id: UUID,
    payload: AskHRRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AskHRResponse:
    """Escalates a Policy Brain message to HR via `TicketService` (`source='chat_ask_hr'`)."""
    return await ChatService.ask_hr_for_message(
        db,
        actor=current_user,
        message_id=message_id,
        payload=payload,
    )


@router.get("/chat/policy-gaps", response_model=PolicyGapsResponse, tags=["chat"])
async def list_policy_gaps(
    current_user: AuthenticatedUserContext = Depends(require_owner_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> PolicyGapsResponse:
    """Aggregates abstained & down-voted Policy Brain messages grouped by `topic_tag` (HR/Owner only)."""
    return await ChatService.get_policy_gaps(db, actor=current_user)
