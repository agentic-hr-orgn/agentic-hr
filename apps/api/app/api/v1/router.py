"""
API V1 router compatibility module re-exporting `api_router`.
Allows importing from either `app.api.v1.api` or `app.api.v1.router`.
"""

from app.api.v1.api import api_router

__all__ = ["api_router"]
