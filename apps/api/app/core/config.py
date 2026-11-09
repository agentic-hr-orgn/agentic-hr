from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directory for locating .env at repository root
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
ENV_PATH = BASE_DIR / ".env"
if not ENV_PATH.exists():
    ENV_PATH = Path(__file__).resolve().parent.parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH) if ENV_PATH.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "Agentic HR Core"
    ENVIRONMENT: str = "development"
    APP_ENV: str = "development"
    DEBUG: bool = True
    API_PORT: int = 8000
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # ADR 006: Stage 1 uses fake integrations
    USE_FAKE_INTEGRATIONS: bool = True

    # ADR 002: Infrastructure (Postgres + pgvector, Redis, Temporal)
    MIGRATION_DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agentic_hr"
    DATABASE_URL: str = (
        "postgresql+asyncpg://agentic_hr_app:agentic_hr_app_secret@localhost:5432/agentic_hr"
    )
    REDIS_URL: str = "redis://localhost:6379/0"
    TEMPORAL_ADDRESS: str = "localhost:7233"
    TEMPORAL_TASK_QUEUE: str = "agentic-hr-task-queue"

    # ADR 003: Model Routing (Gemini 3 Free Tier - Synthetic Data Only)
    STRONG_MODEL: str = "gemini-3.8-flash"
    CHEAP_MODEL: str = "gemini-3.5-flash-lite"
    EMBEDDING_MODEL: str = "gemini-embedding-2"
    STRONG_MODEL_API_KEY: str | None = None
    CHEAP_MODEL_API_KEY: str | None = None
    EMBEDDING_MODEL_API_KEY: str | None = None

    # Stage 2 Phase 11: Central Model Gateway Configuration
    CHEAP_MODEL_PROVIDER: str | None = None
    CHEAP_MODEL_ID: str | None = None
    STRONG_MODEL_PROVIDER: str | None = None
    STRONG_MODEL_ID: str | None = None
    PRIVATE_MODEL_PROVIDER: str | None = None
    PRIVATE_MODEL_ID: str | None = None
    PRIVATE_MODEL_API_KEY: str | None = None
    PRIVATE_MODEL_ENDPOINT: str | None = None
    MODEL_GATEWAY_TIMEOUT_SECONDS: float = 30.0
    MODEL_GATEWAY_MAX_RETRIES: int = 2
    MODEL_GATEWAY_RETRY_BASE_DELAY: float = 0.5
    LANGFUSE_TRACING_ENABLED: bool = False

    # API Keys & Third-party integrations
    GROQ_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None

    # Clerk
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: str | None = None
    CLERK_SECRET_KEY: str | None = None
    CLERK_ISSUER: str = "https://sincere-escargot-959.clerk.accounts.dev"
    CLERK_JWKS_URL: str = "https://sincere-escargot-959.clerk.accounts.dev/.well-known/jwks.json"
    CLERK_AUTHORIZED_PARTY: str | None = None

    # Langfuse
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_BASE_URL: str = "https://us.cloud.langfuse.com"

    # Sentry & E2B
    SENTRY_DSN: str | None = None
    E2B_API_KEY: str | None = None

    # Google OAuth (Send, Drive File, Calendar Events)
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None

    # Sensitive Field Encryption (Stage 2 Phase 9: AES-256-GCM Envelope Encryption)
    FIELD_ENCRYPTION_KEY: str | None = None
    FIELD_ENCRYPTION_KEY_ID: str = "dev-kek-v1"
    FIELD_ENCRYPTION_SECONDARY_KEYS: str | None = None

    # Redis Rate Limiting (Stage 2 Phase 10)
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT_REQUESTS: int = 60
    RATE_LIMIT_DEFAULT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_FAIL_OPEN: bool = True


settings = Settings()
