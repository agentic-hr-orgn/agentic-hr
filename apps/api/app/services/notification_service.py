"""
In-App Notifications Engine for Agentic HR Core (Stage 4 Prompt 5).
Enforces strict tenant isolation and role-based visibility:
- Owner and HR Head see and manage all notifications in their tenant.
- All other users see and manage ONLY notifications addressed to `recipient_user_id == actor.user_id`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.schemas.notification import NotificationItem, NotificationListResponse

if TYPE_CHECKING:
    from app.core.security import AuthenticatedUserContext


class NotificationService:
    """Manages tenant-scoped in-app notifications with deduped inserts and role-based visibility."""

    @staticmethod
    def _is_tenant_wide_viewer(actor: AuthenticatedUserContext | None) -> bool:
        """Owners and HR Heads can view/manage all tenant notifications."""
        if actor is None:
            return True
        return bool(actor.role == "owner" or actor.is_hr_head)

    @classmethod
    def _visibility_filters(
        cls,
        tenant_id: UUID,
        actor: AuthenticatedUserContext | None,
    ) -> list[object]:
        """Builds SQLAlchemy WHERE clauses enforcing tenant + role-based notification visibility."""
        filters: list[object] = [Notification.tenant_id == tenant_id]
        if actor is not None and not cls._is_tenant_wide_viewer(actor):
            filters.append(Notification.recipient_user_id == UUID(str(actor.user_id)))
        return filters

    @staticmethod
    async def create_deduped(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        recipient_user_id: UUID | None,
        recipient_email: str | None,
        category: str,
        severity: str,
        title: str,
        message: str,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        dedupe_key: str | None = None,
    ) -> UUID | None:
        """
        Atomically inserts a notification using PostgreSQL `ON CONFLICT DO NOTHING`
        on the partial unique index `(tenant_id, dedupe_key) WHERE dedupe_key IS NOT NULL`.
        Returns the created notification UUID, or None if already present (deduplicated).
        """
        t_id = UUID(str(tenant_id))
        stmt = pg_insert(Notification).values(
            tenant_id=t_id,
            recipient_user_id=recipient_user_id,
            recipient_email=recipient_email,
            category=category,
            severity=severity,
            title=title[:255],
            message=message,
            entity_type=entity_type,
            entity_id=entity_id,
            dedupe_key=dedupe_key,
            is_read=False,
        )
        if dedupe_key:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["tenant_id", "dedupe_key"],
                index_where=Notification.dedupe_key.is_not(None),
            )
        stmt = stmt.returning(Notification.id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @classmethod
    async def unread_count(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        actor: AuthenticatedUserContext | None = None,
    ) -> int:
        """Returns unread notification count visible to the caller in their tenant."""
        t_id = UUID(str(tenant_id))
        filters = cls._visibility_filters(t_id, actor)
        stmt = select(func.count(Notification.id)).where(
            *filters,
            Notification.is_read.is_(False),
        )
        return int((await session.execute(stmt)).scalar() or 0)

    @classmethod
    async def list_notifications(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        actor: AuthenticatedUserContext | None = None,
        unread_only: bool = False,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> NotificationListResponse:
        """Lists visible notifications for the tenant with total and unread_count."""
        t_id = UUID(str(tenant_id))
        safe_limit = max(1, min(100, limit))
        safe_offset = max(0, offset)

        base_filters = cls._visibility_filters(t_id, actor)
        query_filters = list(base_filters)
        if unread_only:
            query_filters.append(Notification.is_read.is_(False))
        if category and category.strip():
            query_filters.append(Notification.category == category.strip().lower())

        total_stmt = select(func.count(Notification.id)).where(*query_filters)
        total = int((await session.execute(total_stmt)).scalar() or 0)

        unread = await cls.unread_count(session, tenant_id=t_id, actor=actor)

        items_stmt = (
            select(Notification)
            .where(*query_filters)
            .order_by(desc(Notification.created_at), desc(Notification.id))
            .limit(safe_limit)
            .offset(safe_offset)
        )
        rows = (await session.execute(items_stmt)).scalars().all()
        items = [NotificationItem.model_validate(r) for r in rows]

        return NotificationListResponse(
            items=items,
            total=total,
            unread_count=unread,
        )

    @classmethod
    async def mark_read(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        notification_id: UUID,
        actor: AuthenticatedUserContext | None = None,
    ) -> NotificationItem:
        """
        Marks a single visible notification as read.
        Enforces identical role-based visibility predicate (returns 404 if not visible/found).
        """
        t_id = UUID(str(tenant_id))
        filters = cls._visibility_filters(t_id, actor)
        stmt = select(Notification).where(
            *filters,
            Notification.id == notification_id,
        )
        notif = (await session.execute(stmt)).scalar_one_or_none()
        if not notif:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Notification '{notification_id}' not found.",
            )

        if not notif.is_read:
            notif.is_read = True
            notif.read_at = datetime.now(UTC)
            await session.flush()

        return NotificationItem.model_validate(notif)

    @classmethod
    async def mark_all_read(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        actor: AuthenticatedUserContext | None = None,
    ) -> int:
        """Marks all unread notifications visible to the caller as read and returns count updated."""
        t_id = UUID(str(tenant_id))
        filters = cls._visibility_filters(t_id, actor)
        now = datetime.now(UTC)
        stmt = (
            update(Notification)
            .where(*filters, Notification.is_read.is_(False))
            .values(is_read=True, read_at=now)
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount or 0)
