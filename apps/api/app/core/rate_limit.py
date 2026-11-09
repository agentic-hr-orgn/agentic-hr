"""
Reusable Multi-Tenant Redis Rate Limiting Service for Agentic HR Core.
Complies with ADR 002 (Infrastructure), ADR 003, and Stage 2 Phase 10 specifications.

Features:
- Multi-tenant and per-user key namespacing: rl:{tenant_id}:{user_id}:{scope}.
- Atomic sliding-window log via embedded Redis Lua script.
- Consistent HTTP 429 Too Many Requests response with standard Retry-After headers.
- Graceful degradation when Redis is unavailable (configurable fail-open vs fail-closed).
- Explicit exemptions for public health probes and authentication webhooks.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, Response, status
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import settings
from app.core.redis import get_redis_client

logger = logging.getLogger("agentic_hr.rate_limit")

# Exempt paths that bypass rate limiting
EXEMPT_PATH_PREFIXES = (
    "/health",
    "/api/health",
    "/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/auth/clerk-webhook",
)

# Atomic sliding-window rate-limiting Lua script
SLIDING_WINDOW_LUA_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local member_id = ARGV[4]
local clear_before = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', key, '-inf', clear_before)
local current_count = redis.call('ZCARD', key)

if current_count < max_requests then
    redis.call('ZADD', key, now_ms, member_id)
    local ttl_sec = math.ceil(window_ms / 1000) + 1
    redis.call('EXPIRE', key, ttl_sec)
    local remaining = max_requests - current_count - 1
    return {1, remaining, 0}
else
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = 1
    if #oldest > 1 then
        local oldest_ts = tonumber(oldest[2])
        local reset_at = oldest_ts + window_ms
        if reset_at > now_ms then
            retry_after = math.ceil((reset_at - now_ms) / 1000)
            if retry_after < 1 then retry_after = 1 end
        end
    end
    return {0, 0, retry_after}
end
"""


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of a rate-limit quota check."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    reset_epoch: int
    degraded: bool = False


async def check_rate_limit(
    tenant_id: str,
    user_id: str | None = None,
    scope: str = "default",
    max_requests: int = 60,
    window_seconds: int = 60,
    redis_client: Any = None,
) -> RateLimitResult:
    """
    Evaluates rate-limiting quota atomically using Redis Lua script.
    Guarantees strict tenant isolation by namespacing keys per tenant.
    """
    now = time.time()
    now_ms = int(now * 1000)
    window_ms = window_seconds * 1000
    reset_epoch = int(now + window_seconds)
    user_segment = user_id if user_id else "tenant_shared"
    key = f"rl:{tenant_id}:{user_segment}:{scope}"
    member_id = f"{now_ms}:{uuid.uuid4().hex[:8]}"

    try:
        client = redis_client if redis_client is not None else await get_redis_client()
        res = await client.eval(
            SLIDING_WINDOW_LUA_SCRIPT,
            1,
            key,
            str(now_ms),
            str(window_ms),
            str(max_requests),
            member_id,
        )
        is_allowed = bool(res[0] == 1)
        remaining = int(res[1])
        retry_after = int(res[2])

        return RateLimitResult(
            allowed=is_allowed,
            limit=max_requests,
            remaining=remaining,
            retry_after=retry_after,
            reset_epoch=reset_epoch,
            degraded=False,
        )
    except (RedisConnectionError, RedisTimeoutError, RedisError) as exc:
        logger.warning(f"Redis rate limiter unavailable for key '{key}': {exc}")
        if settings.RATE_LIMIT_FAIL_OPEN:
            return RateLimitResult(
                allowed=True,
                limit=max_requests,
                remaining=max_requests - 1,
                retry_after=0,
                reset_epoch=reset_epoch,
                degraded=True,
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiter service temporarily unavailable.",
        ) from exc


class RateLimiter:
    """
    FastAPI dependency factory enforcing rate limits on endpoints.
    Can be parameterized with custom request counts, window durations, and scope tags.
    """

    def __init__(
        self,
        requests: int | None = None,
        window_seconds: int | None = None,
        scope: str = "default",
        by_user: bool = True,
    ) -> None:
        self.requests = requests or settings.RATE_LIMIT_DEFAULT_REQUESTS
        self.window_seconds = window_seconds or settings.RATE_LIMIT_DEFAULT_WINDOW_SECONDS
        self.scope = scope
        self.by_user = by_user

    async def __call__(self, request: Request, response: Response) -> RateLimitResult:
        if not settings.RATE_LIMIT_ENABLED:
            return RateLimitResult(
                allowed=True,
                limit=self.requests,
                remaining=self.requests,
                retry_after=0,
                reset_epoch=int(time.time() + self.window_seconds),
                degraded=False,
            )

        # Check exempt path prefixes
        path = request.url.path
        if any(path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES):
            return RateLimitResult(
                allowed=True,
                limit=self.requests,
                remaining=self.requests,
                retry_after=0,
                reset_epoch=int(time.time() + self.window_seconds),
                degraded=False,
            )

        # Extract tenant and user identifiers from request state, authorization header, or fallback to client IP
        current_user = getattr(request.state, "current_user", None)
        if current_user and getattr(current_user, "tenant_id", None):
            tenant_id = str(current_user.tenant_id)
            user_id = str(current_user.user_id) if self.by_user else None
        else:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                try:
                    import jwt

                    token = auth_header.split(" ", 1)[1]
                    unverified = jwt.decode(token, options={"verify_signature": False})
                    tenant_id = str(unverified.get("org_id", "default_org"))
                    user_id = str(unverified.get("sub", "default_user")) if self.by_user else None
                except Exception:
                    tenant_id = "ip"
                    user_id = request.client.host if request.client else "127.0.0.1"
            else:
                client_host = request.client.host if request.client else "127.0.0.1"
                tenant_id = "ip"
                user_id = client_host

        result = await check_rate_limit(
            tenant_id=tenant_id,
            user_id=user_id,
            scope=self.scope,
            max_requests=self.requests,
            window_seconds=self.window_seconds,
        )

        # Inject standard rate-limit headers
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Reset"] = str(result.reset_epoch)

        if result.degraded:
            response.headers["X-RateLimit-Degraded"] = "true"

        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {result.retry_after} seconds.",
                headers={
                    "Retry-After": str(result.retry_after),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(result.reset_epoch),
                },
            )

        return result
