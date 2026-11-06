"""
SQLAlchemy 2.0 Declarative Base for Agentic HR Core.
Serves as the root metadata container for all entity models and Alembic migrations.
"""

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase


class Base(AsyncAttrs, DeclarativeBase):
    """Modern SQLAlchemy 2.0 DeclarativeBase with async attributes support."""
