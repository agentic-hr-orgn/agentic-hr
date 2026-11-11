"""
Redis connection management and health checks for Agentic HR Core.
Complies with ADR 002 (Infrastructure) and Stage 2 Phase 10 specifications.
"""

import asyncio
import logging

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import settings

logger = logging.getLogger("agentic_hr.redis")

_redis_pools: dict[asyncio.AbstractEventLoop | None, aioredis.ConnectionPool] = {}
_redis_clients: dict[asyncio.AbstractEventLoop | None, aioredis.Redis] = {}


def get_redis_pool() -> aioredis.ConnectionPool:
    """Returns or initializes an async Redis connection pool bound to the running event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop not in _redis_pools:
        _redis_pools[loop] = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=50,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
    return _redis_pools[loop]


async def get_redis_client() -> aioredis.Redis:
    """Provides an async Redis client backed by the event-loop-scoped connection pool."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop not in _redis_clients:
        _redis_clients[loop] = aioredis.Redis(connection_pool=get_redis_pool())
    return _redis_clients[loop]


async def close_redis() -> None:
    """Gracefully disconnects and clears the Redis connection pool for the running event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    client = _redis_clients.pop(loop, None)
    if client is not None:
        try:
            await client.aclose()
        except Exception as exc:
            logger.warning(f"Error closing Redis client: {exc}")

    pool = _redis_pools.pop(loop, None)
    if pool is not None:
        try:
            await pool.disconnect()
            logger.info("Redis connection pool closed.")
        except Exception as exc:
            logger.warning(f"Error disconnecting Redis pool: {exc}")


async def is_redis_healthy() -> bool:
    """Probes Redis server with PING to verify availability."""
    try:
        client = await get_redis_client()
        pong = await client.ping()
        return bool(pong)
    except (RedisConnectionError, RedisTimeoutError, RedisError) as exc:
        logger.debug(f"Redis health check failed: {exc}")
        return False
    except Exception as exc:
        logger.warning(f"Unexpected error during Redis health check: {exc}")
        return False
