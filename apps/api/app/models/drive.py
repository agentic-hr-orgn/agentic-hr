"""
Google Drive File Metadata Model for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and ADR 006 (drive.file scope least privilege).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DriveFileRecord(Base):
    """
    Metadata record for files created or uploaded to Google Drive under drive.file scope.
    Protected by PostgreSQL Row-Level Security (RLS).
    """

    __tablename__ = "drive_files"
    __table_args__ = (
        Index("ix_drive_files_tenant_id", "tenant_id"),
        Index("ix_drive_files_drive_file_id", "drive_file_id"),
        Index("ix_drive_files_tenant_folder", "tenant_id", "folder_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        comment="Tenant scope for RLS isolation",
    )
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connector_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    drive_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    web_view_link: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
