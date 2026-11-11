"""
Server-Bound MCP Execution Context & PostgreSQL RLS Integration.
Ensures that all MCP tool executions obtain authoritative identity and tenancy
from server-managed state rather than trusting raw LLM arguments.
"""

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.mcp.exceptions import MCPContextMissingError
from app.models.tenant import set_tenant_context


class MCPExecutionContext(BaseModel):
    """
    Authoritative server-side execution context for Model Context Protocol tools.
    Bound per-request or per-agent-turn; cannot be forged by LLM prompt arguments.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    user_id: UUID
    role: str = "employee"  # "owner", "manager", "employee"
    is_hr_head: bool = False
    is_payroll_admin: bool = False
    untrusted_context: bool = False  # Rule 6: True when processing external CVs, emails, etc.
    credential_id: UUID | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


current_mcp_context: ContextVar[MCPExecutionContext | None] = ContextVar(
    "current_mcp_context", default=None
)


@contextmanager
def mcp_execution_context(ctx: MCPExecutionContext) -> Iterator[MCPExecutionContext]:
    """
    Synchronous context manager setting the active server-bound MCPExecutionContext.
    Restores previous context upon exit.
    """
    token: Token[MCPExecutionContext | None] = current_mcp_context.set(ctx)
    try:
        yield ctx
    finally:
        current_mcp_context.reset(token)


def get_current_mcp_context() -> MCPExecutionContext:
    """
    Retrieves the active server-bound MCP execution context.
    Raises MCPContextMissingError if no context is bound.
    """
    ctx = current_mcp_context.get()
    if ctx is None:
        raise MCPContextMissingError(
            "MCP tool invoked without an active server-bound MCPExecutionContext. "
            "Execution rejected to prevent unauthenticated or cross-tenant access."
        )
    return ctx


@asynccontextmanager
async def get_mcp_session(
    ctx: MCPExecutionContext | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager yielding an AsyncSession with tenant RLS context pre-set.
    Commits on clean exit, rolls back on exception.
    """
    effective_ctx = ctx or get_current_mcp_context()
    async with AsyncSessionLocal() as session:
        await set_tenant_context(
            session,
            str(effective_ctx.tenant_id),
            str(effective_ctx.user_id),
        )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
