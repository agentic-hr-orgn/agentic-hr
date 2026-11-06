"""API Routers."""

from app.routers.health import router as health_router
from app.routers.synthetic import router as synthetic_router

__all__ = ["health_router", "synthetic_router"]
