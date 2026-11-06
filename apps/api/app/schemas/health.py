"""
Pydantic Schemas for System Health and Diagnostics.
Used by GET /health and monitoring probes.
"""

from pydantic import BaseModel, ConfigDict


class DatabaseStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connected: bool
    pgvector_enabled: bool


class ModelRoutingInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cheap: str
    strong: str
    embedding: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = "healthy"
    stage: str = "Stage 1: Platform Skeleton"
    environment: str
    version: str = "0.1.0"
    use_fake_integrations: bool
    database_connected: bool
    database: DatabaseStatus
    models: ModelRoutingInfo
    request_id: str | None = None
