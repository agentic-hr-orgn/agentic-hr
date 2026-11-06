"""
Database adapter bridge for backwards compatibility within app.core.
Delegates to app.db.session and app.db.base.
"""

from app.db.base import Base
from app.db.session import (
    AsyncSessionLocal,
    check_db_health,
    engine,
    get_db_session,
)

get_db = get_db_session

__all__ = [
    "AsyncSessionLocal",
    "Base",
    "check_db_health",
    "engine",
    "get_db",
    "get_db_session",
]
