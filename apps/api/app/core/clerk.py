"""
Clerk Authentication & JWKS Token Verification Engine for Agentic HR Core.
Complies with ADR 002, ADR 006, and Clerk OpenID Connect / JWKS specifications.
Validates RS256 JWT signatures against Clerk JWKS, enforces exp/nbf/iss/azp,
and extracts verified subject (user) and organization claims.
"""

import logging
from typing import Any

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from app.core.config import settings

logger = logging.getLogger("agentic_hr.clerk")

# Global PyJWKClient cache
_jwks_client: PyJWKClient | None = None
_jwks_override_keys: dict[str, Any] = {}


def get_jwks_client() -> PyJWKClient:
    """Returns or initializes the singleton PyJWKClient for Clerk JWKS keys."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            settings.CLERK_JWKS_URL,
            cache_keys=True,
            max_cached_keys=16,
            cache_jwk_set=True,
            lifespan=3600,
        )
    return _jwks_client


def register_test_signing_key(kid: str, public_key: Any) -> None:
    """Allows test fixtures to register local public RSA keys for offline JWT testing."""
    _jwks_override_keys[kid] = public_key


def clear_test_signing_keys() -> None:
    """Clears registered test keys."""
    _jwks_override_keys.clear()


def parse_mock_token(token: str) -> dict[str, Any] | None:
    """
    Parses deterministic mock test tokens (e.g., 'mock:user_email:org_id' or 'mock:user_id').
    Only permitted in development, test, or when USE_FAKE_INTEGRATIONS=true.
    """
    if not token.startswith("mock:"):
        return None

    if not (settings.USE_FAKE_INTEGRATIONS or settings.ENVIRONMENT in ("development", "test")):
        logger.warning("Mock authentication tokens are strictly forbidden in production mode.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mock authentication tokens are disabled in this environment.",
        )

    parts = token.split(":")
    # Expected formats:
    # mock:<user_id_or_email>
    # mock:<user_id_or_email>:<org_id>
    if len(parts) < 2:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed mock token format. Expected 'mock:<user_id_or_email>[:<org_id>]'.",
        )

    sub = parts[1]
    org_id = parts[2] if len(parts) > 2 and parts[2].strip() else None

    return {
        "sub": sub,
        "org_id": org_id,
        "iss": settings.CLERK_ISSUER,
        "aud": "agentic-hr-mock",
        "mock": True,
    }


def verify_clerk_token(token: str) -> dict[str, Any]:
    """
    Cryptographically verifies a Clerk JWT session token.
    1. Checks for mock tokens in dev/test/fake mode.
    2. Validates RS256 signature against Clerk JWKS or registered test key.
    3. Validates expiration (exp), not-before (nbf), issuer (iss), and authorized party (azp).
    4. Returns verified claims payload dictionary.
    """
    # 1. Check for mock token format
    mock_claims = parse_mock_token(token)
    if mock_claims is not None:
        return mock_claims

    # 2. Extract and inspect token header
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        logger.warning(f"Failed to decode JWT header: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format: malformed JWT header.",
        ) from exc

    kid = header.get("kid")
    alg = header.get("alg", "RS256")

    if alg != "RS256":
        logger.warning(f"Rejected unsupported JWT algorithm: {alg}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unsupported token algorithm '{alg}'. Only RS256 is permitted.",
        )

    # 3. Resolve signing key
    key: Any = None
    if kid and kid in _jwks_override_keys:
        key = _jwks_override_keys[kid]
    else:
        try:
            jwks = get_jwks_client()
            signing_key = jwks.get_signing_key_from_jwt(token)
            key = signing_key.key
        except Exception as exc:
            logger.warning(f"Failed to resolve signing key from Clerk JWKS: {exc}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to verify token signature: public key resolution failed.",
            ) from exc

    # 4. Decode and verify signature, exp, nbf, and iss
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.CLERK_ISSUER,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        logger.info("Rejected expired JWT token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired.",
        ) from exc
    except jwt.ImmatureSignatureError as exc:
        logger.info("Rejected immature JWT token (nbf in future)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token is not yet valid.",
        ) from exc
    except jwt.InvalidIssuerError as exc:
        logger.warning(f"Rejected JWT token with invalid issuer: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has an invalid issuer.",
        ) from exc
    except jwt.InvalidSignatureError as exc:
        logger.warning("Rejected JWT token with invalid signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token signature verification failed.",
        ) from exc
    except jwt.PyJWTError as exc:
        logger.warning(f"JWT verification error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication token verification failed: {exc}",
        ) from exc

    # 5. Validate authorized party (azp) if configured
    if settings.CLERK_AUTHORIZED_PARTY:
        azp = payload.get("azp")
        if azp != settings.CLERK_AUTHORIZED_PARTY:
            logger.warning(
                f"Token azp '{azp}' does not match expected '{settings.CLERK_AUTHORIZED_PARTY}'"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication token authorized party mismatch.",
            )

    return payload
