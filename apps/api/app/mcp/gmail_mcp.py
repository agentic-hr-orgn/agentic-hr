"""
Gmail FastMCP Server for Agentic HR Core.
Exposes send_email_tool and list_sent_messages_tool under ADR 006 send-only least-privilege scope.
"""

from typing import Any

from sqlalchemy import desc, select

from app.mcp.base import FastMCP
from app.mcp.context import get_current_mcp_context, get_mcp_session
from app.mcp.guardrails import mcp_guardrail
from app.models.connector import SentMessage
from app.services.email_delivery_service import EmailDeliveryService

gmail_mcp_server = FastMCP(
    name="agentic-hr-gmail",
    instructions="Gmail tools for outbound HR communication, interview scheduling, and notifications. Operates under send-only scope.",
)


@gmail_mcp_server.tool(
    name="send_email_tool",
    description="Sends an outbound email via Gmail connector. Enforces read-only check, sliding-window rate limit, token refresh, and idempotency.",
)
@mcp_guardrail("send_email_tool", is_write=True)
async def send_email_tool(
    to_email: str,
    subject: str,
    body_text: str,
    template_name: str | None = None,
    template_variables: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    force_retry: bool = False,
) -> dict[str, Any]:
    """
    Sends an email to the specified recipient using server-bound tenant identity.
    """
    ctx = get_current_mcp_context()
    async with get_mcp_session(ctx) as session:
        result = await EmailDeliveryService.send_email(
            session=session,
            tenant_id=ctx.tenant_id,
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            template_name=template_name,
            template_variables=template_variables,
            idempotency_key=idempotency_key,
            credential_id=ctx.credential_id,
            actor_id=ctx.user_id,
            force_retry=force_retry,
        )
        return result


@gmail_mcp_server.tool(
    name="list_sent_messages_tool",
    description="Lists sent email delivery records and statuses for the active tenant. Does not read user inbox.",
)
@mcp_guardrail("list_sent_messages_tool", is_write=False)
async def list_sent_messages_tool(
    status: str | None = None,
    recipient_email: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Queries outbound sent messages log for active tenant.
    """
    ctx = get_current_mcp_context()
    async with get_mcp_session(ctx) as session:
        stmt = select(SentMessage).where(SentMessage.tenant_id == ctx.tenant_id)
        if status:
            stmt = stmt.where(SentMessage.status == status.strip().lower())
        if recipient_email:
            stmt = stmt.where(SentMessage.recipient_email.ilike(f"%{recipient_email.strip()}%"))

        stmt = stmt.order_by(desc(SentMessage.created_at)).limit(limit).offset(offset)
        result = await session.execute(stmt)
        messages = result.scalars().all()

        return [
            {
                "id": str(msg.id),
                "tenant_id": str(msg.tenant_id),
                "recipient_email": msg.recipient_email,
                "subject": msg.subject,
                "template_name": msg.template_name,
                "provider_message_id": msg.provider_message_id,
                "status": msg.status,
                "error_message": msg.error_message,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
            for msg in messages
        ]
