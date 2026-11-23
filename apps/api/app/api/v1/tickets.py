"""
Stage 7 Helpdesk Ticketing, Assignment Rules & Read-Only Agent REST API Router.

Mounted at `/api/v1` (via `app.api.v1.api.api_router` in `app.main`):
- POST /api/v1/tickets
- GET  /api/v1/tickets/me
- GET  /api/v1/tickets/queue
- GET  /api/v1/tickets/rules
- PUT  /api/v1/tickets/rules/{category}
- GET  /api/v1/tickets/summary
- GET  /api/v1/tickets/{ticket_id}
- PATCH /api/v1/tickets/{ticket_id}
- POST /api/v1/tickets/{ticket_id}/messages
- POST /api/v1/tickets/{ticket_id}/rate
- POST /api/v1/tickets/{ticket_id}/reopen
- POST /api/v1/helpdesk/ask
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import RateLimiter
from app.core.security import (
    AuthenticatedUserContext,
    get_current_user,
    require_hr_head,
    require_owner_manager_or_hr_head,
)
from app.db.session import get_db
from app.schemas.ticket import (
    HelpdeskAskRequest,
    HelpdeskAskResponse,
    TicketAssignmentRuleResponse,
    TicketCategory,
    TicketCloseRequest,
    TicketCreateRequest,
    TicketDetailResponse,
    TicketMessageCreateRequest,
    TicketMessageResponse,
    TicketPriority,
    TicketReopenRequest,
    TicketResponse,
    TicketRuleUpdateRequest,
    TicketStatus,
    TicketSummaryResponse,
    TicketUpdateRequest,
)
from app.services.helpdesk_agent_service import HelpdeskAgentService
from app.services.ticket_service import TicketService

router = APIRouter()


@router.post(
    "/tickets",
    response_model=TicketResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["tickets"],
)
async def create_ticket(
    payload: TicketCreateRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    """Creates a Helpdesk ticket, routes to category assignee, and starts SLA workflow."""
    return await TicketService.create_ticket(db, actor=current_user, payload=payload)


@router.get("/tickets/me", response_model=list[TicketResponse], tags=["tickets"])
async def list_my_tickets(
    status_filter: TicketStatus | None = Query(default=None, alias="status"),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TicketResponse]:
    """Lists only the caller's own submitted Helpdesk tickets."""
    return await TicketService.list_my_tickets(
        db,
        actor=current_user,
        status_filter=status_filter,
    )


@router.get("/tickets/queue", response_model=list[TicketResponse], tags=["tickets"])
async def list_staff_ticket_queue(
    status_filter: TicketStatus | None = Query(default=None, alias="status"),
    category: TicketCategory | None = Query(default=None),
    priority: TicketPriority | None = Query(default=None),
    assigned_to_user_id: UUID | None = Query(default=None),
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> list[TicketResponse]:
    """Lists the Helpdesk staff queue across the tenant with optional filters."""
    return await TicketService.list_staff_queue(
        db,
        actor=current_user,
        status_filter=status_filter,
        category_filter=category,
        priority_filter=priority,
        assigned_to_user_id=assigned_to_user_id,
    )


@router.get(
    "/tickets/rules",
    response_model=list[TicketAssignmentRuleResponse],
    tags=["tickets"],
)
async def list_ticket_assignment_rules(
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> list[TicketAssignmentRuleResponse]:
    """Lists per-category ticket routing and SLA configuration (Owner / HR Head only)."""
    return await TicketService.list_rules(db, actor=current_user)


@router.put(
    "/tickets/rules/{category}",
    response_model=TicketAssignmentRuleResponse,
    tags=["tickets"],
)
async def update_ticket_assignment_rule(
    category: TicketCategory,
    payload: TicketRuleUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_hr_head),
    db: AsyncSession = Depends(get_db),
) -> TicketAssignmentRuleResponse:
    """Configures routing assignee, SLA hours, reminder lead time, and reopen window."""
    return await TicketService.upsert_rule(
        db,
        actor=current_user,
        category=category,
        payload=payload,
    )


@router.get("/tickets/summary", response_model=list[TicketSummaryResponse], tags=["tickets"])
async def list_ticket_summary(
    status_filter: TicketStatus | None = Query(default=None, alias="status"),
    category: TicketCategory | None = Query(default=None),
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TicketSummaryResponse]:
    """Queries secure view `public.pub_ticket_summary` (strictly excludes internal notes)."""
    return await TicketService.list_ticket_summary(
        db,
        actor=current_user,
        status_filter=status_filter,
        category_filter=category,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse, tags=["tickets"])
async def get_ticket_detail(
    ticket_id: UUID,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketDetailResponse:
    """Returns ticket detail + thread (internal notes stripped for plain employees)."""
    return await TicketService.get_ticket_detail(
        db,
        actor=current_user,
        ticket_id=ticket_id,
    )


@router.patch("/tickets/{ticket_id}", response_model=TicketResponse, tags=["tickets"])
async def update_ticket(
    ticket_id: UUID,
    payload: TicketUpdateRequest,
    current_user: AuthenticatedUserContext = Depends(require_owner_manager_or_hr_head),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    """Updates ticket status, priority, category, or assignee (Helpdesk staff only)."""
    return await TicketService.update_ticket(
        db,
        actor=current_user,
        ticket_id=ticket_id,
        payload=payload,
    )


@router.post(
    "/tickets/{ticket_id}/messages",
    response_model=TicketMessageResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["tickets"],
)
async def add_ticket_message(
    ticket_id: UUID,
    payload: TicketMessageCreateRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketMessageResponse:
    """Posts a public reply (or HR/staff internal note; forced false for employees)."""
    return await TicketService.add_message(
        db,
        actor=current_user,
        ticket_id=ticket_id,
        payload=payload,
    )


@router.post("/tickets/{ticket_id}/rate", response_model=TicketResponse, tags=["tickets"])
async def rate_ticket(
    ticket_id: UUID,
    payload: TicketCloseRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    """Submits requester CSAT rating (`1..5`) on a resolved or closed ticket."""
    return await TicketService.rate_ticket(
        db,
        actor=current_user,
        ticket_id=ticket_id,
        payload=payload,
    )


@router.post("/tickets/{ticket_id}/reopen", response_model=TicketResponse, tags=["tickets"])
async def reopen_ticket(
    ticket_id: UUID,
    payload: TicketReopenRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    """Reopens a resolved/closed ticket while within its `reopen_until` window (`409` after)."""
    return await TicketService.reopen_ticket(
        db,
        actor=current_user,
        ticket_id=ticket_id,
        payload=payload,
    )


@router.post(
    "/helpdesk/ask",
    response_model=HelpdeskAskResponse,
    tags=["helpdesk"],
    dependencies=[Depends(RateLimiter(requests=30, window_seconds=60, scope="helpdesk.ask"))],
)
async def ask_helpdesk_agent(
    payload: HelpdeskAskRequest,
    current_user: AuthenticatedUserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HelpdeskAskResponse:
    """
    Read-only Helpdesk KB assistant:
    - Answers with verified `helpdesk_kb` citations when grounded.
    - Blocks privileged action requests and opens a routed ticket instead.
    - Escalates unanswerable questions into a categorized, prioritized ticket.
    """
    return await HelpdeskAgentService.ask(db, actor=current_user, payload=payload)
