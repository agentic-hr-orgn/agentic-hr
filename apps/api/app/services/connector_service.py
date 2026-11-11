"""
Connector Credential Vault and Scope Governance Service for Agentic HR Core.
Complies with ADR 002 (Multi-Tenancy) and ADR 006 (Google Workspace Integration).

Features:
- Strict scope whitelisting & rejection of prohibited read/modify inbox scopes.
- AES-256-GCM envelope encryption for stored access and refresh tokens.
- Per-tenant read-only mode toggle with client-enforced checks.
- Automatic transition to needs_reconnect and job pausing on 401 / invalid_grant.
- Cryptographically signed HMAC CSRF state tokens for OAuth authorize / callback flows.
- Immutable audit logging on all credential lifecycle transitions.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.encryption import EncryptionContext, decrypt_field, encrypt_field
from app.models.connector import ConnectorCredential
from app.services.audit_service import write_audit

logger = logging.getLogger("agentic_hr.connectors")

# ADR 006 Allowed Minimum Google Scopes
ALLOWED_GOOGLE_SCOPES_MAP = {
    "gmail.send": "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.send": "https://www.googleapis.com/auth/gmail.send",
    "drive.file": "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.file": "https://www.googleapis.com/auth/drive.file",
    "calendar.events": "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.events": "https://www.googleapis.com/auth/calendar.events",
}

# Strictly prohibited scopes per ADR 006 & docs/oauth-scopes.md
PROHIBITED_SCOPES = {
    "gmail.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "gmail.modify",
    "https://www.googleapis.com/auth/gmail.modify",
    "mail.google.com",
    "https://mail.google.com/",
}


class ConnectorError(Exception):
    """Base exception for connector operations."""


class ProhibitedScopeError(ConnectorError):
    """Raised when an application requests prohibited read/modify email scopes."""


class InvalidScopeError(ConnectorError):
    """Raised when an unrecognized or unauthorized scope is requested."""


class CredentialNotFoundError(ConnectorError):
    """Raised when a requested credential does not exist or belongs to another tenant."""


class ReadOnlyConnectorError(ConnectorError):
    """Raised when a mutating operation is attempted while connector is in read-only mode."""


class ConnectorAuthExpiredError(ConnectorError):
    """Raised when provider returns HTTP 401 or invalid_grant."""


class ConnectorRateLimitError(ConnectorError):
    """Raised when provider returns HTTP 429 or rate limits are exceeded."""

    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 1) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ConnectorServiceUnavailableError(ConnectorError):
    """Raised when provider returns HTTP 503 or transient network timeout occurs."""


class OAuthStateInvalidError(ConnectorError):
    """Raised when OAuth CSRF state verification fails or expires."""


def validate_scopes(scopes: list[str]) -> list[str]:
    """
    Validates and canonicalizes OAuth scopes.
    Raises ProhibitedScopeError if any prohibited scope is present.
    Raises InvalidScopeError if any scope is not in the allowed whitelist.
    """
    if not scopes:
        raise InvalidScopeError("At least one OAuth scope must be requested.")

    canonical_scopes: list[str] = []
    for s in scopes:
        s_clean = s.strip()
        # Check against blacklist first
        for prohibited in PROHIBITED_SCOPES:
            if s_clean.lower() == prohibited.lower() or prohibited.lower() in s_clean.lower():
                raise ProhibitedScopeError(
                    f"Prohibited OAuth scope requested: '{s_clean}'. "
                    "Inbox reading/modifying scopes are strictly forbidden by ADR 006."
                )

        # Check whitelist
        if s_clean in ALLOWED_GOOGLE_SCOPES_MAP:
            canonical = ALLOWED_GOOGLE_SCOPES_MAP[s_clean]
            if canonical not in canonical_scopes:
                canonical_scopes.append(canonical)
        else:
            raise InvalidScopeError(
                f"Scope '{s_clean}' is not in the approved least-privilege whitelist: "
                f"{list(ALLOWED_GOOGLE_SCOPES_MAP.keys())}."
            )

    return canonical_scopes


def generate_oauth_state(tenant_id: UUID | str, user_id: UUID | str) -> str:
    """
    Generates a cryptographically signed HMAC-SHA256 CSRF state token.
    Token binds tenant_id, user_id, timestamp, and a random nonce.
    Valid for 10 minutes (600 seconds).
    """
    t_id = str(tenant_id).strip()
    u_id = str(user_id).strip()
    ts = int(time.time())
    nonce = secrets.token_hex(16)

    payload = json.dumps({"t": t_id, "u": u_id, "ts": ts, "n": nonce}, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8").rstrip("=")

    secret_key = settings.SECRET_KEY.encode("utf-8")
    signature = hmac.new(secret_key, payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

    return f"{payload_b64}.{sig_b64}"


def verify_oauth_state(state: str, max_age_seconds: int = 600) -> tuple[UUID, UUID]:
    """
    Verifies the HMAC signature and timestamp of an OAuth CSRF state token.
    Returns (tenant_id, user_id).
    """
    if not state or "." not in state:
        raise OAuthStateInvalidError("Malformed OAuth state token.")

    payload_b64, sig_b64 = state.split(".", 1)

    # 1. Verify HMAC signature in constant time
    secret_key = settings.SECRET_KEY.encode("utf-8")
    expected_sig = hmac.new(secret_key, payload_b64.encode("utf-8"), hashlib.sha256).digest()
    expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("utf-8").rstrip("=")

    if not hmac.compare_digest(sig_b64, expected_sig_b64):
        raise OAuthStateInvalidError("Invalid OAuth state signature (tampered token).")

    # 2. Decode payload
    try:
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        raw_json = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
        data = json.loads(raw_json)
        ts = int(data["ts"])
        tenant_id = UUID(data["t"])
        user_id = UUID(data["u"])
    except Exception as exc:
        raise OAuthStateInvalidError(f"Failed to parse OAuth state payload: {exc}") from exc

    # 3. Check expiration
    now = int(time.time())
    if now - ts > max_age_seconds:
        raise OAuthStateInvalidError("OAuth state token has expired.")
    if ts > now + 60:
        raise OAuthStateInvalidError("OAuth state token has future timestamp.")

    return tenant_id, user_id


def build_google_authorization_url(tenant_id: UUID | str, user_id: UUID | str) -> str:
    """
    Generates an offline-access Google OAuth authorization URL with signed state.
    Restricted strictly to the approved least-privilege scopes.
    """
    state_token = generate_oauth_state(tenant_id, user_id)
    scopes_str = (
        "https://www.googleapis.com/auth/gmail.send "
        "https://www.googleapis.com/auth/drive.file "
        "https://www.googleapis.com/auth/calendar.events"
    )

    client_id = settings.GOOGLE_CLIENT_ID or "fake-client-id.apps.googleusercontent.com"
    params = {
        "client_id": client_id,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": scopes_str,
        "access_type": "offline",
        "prompt": "consent",
        "state": state_token,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"


class ConnectorService:
    """
    Core service managing connector credentials lifecycle, encryption, and state transitions.
    """

    @staticmethod
    async def store_credentials(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        provider: str,
        account_email: str,
        scopes: list[str],
        access_token: str,
        refresh_token: str | None = None,
        token_expiry: datetime | None = None,
        actor_id: UUID | str | None = None,
    ) -> ConnectorCredential:
        """
        Stores or updates connector credentials using AES-256-GCM envelope encryption.
        Validates scopes against prohibited inbox-reading lists.
        Emits immutable audit log.
        """
        t_id = UUID(str(tenant_id))
        clean_email = account_email.strip().lower()
        clean_provider = provider.strip().lower()

        # 1. Validate scopes
        canonical_scopes = validate_scopes(scopes)

        # 2. Check existing record
        stmt = select(ConnectorCredential).where(
            ConnectorCredential.tenant_id == t_id,
            ConnectorCredential.provider == clean_provider,
            ConnectorCredential.account_email == clean_email,
        )
        result = await session.execute(stmt)
        cred = result.scalar_one_or_none()

        cred_id = cred.id if cred else uuid.uuid4()

        # 3. Encrypt access token with AES-256-GCM envelope encryption
        ctx_access = EncryptionContext(
            tenant_id=t_id,
            entity_type="connector_credential",
            record_id=cred_id,
            field_name="access_token",
        )
        encrypted_access = encrypt_field(access_token, ctx_access)

        # 4. Encrypt refresh token if provided
        encrypted_refresh: str | None = None
        if refresh_token:
            ctx_refresh = EncryptionContext(
                tenant_id=t_id,
                entity_type="connector_credential",
                record_id=cred_id,
                field_name="refresh_token",
            )
            encrypted_refresh = encrypt_field(refresh_token, ctx_refresh)
        elif cred and cred.encrypted_refresh_token:
            # Preserve existing refresh token on re-authorization without new refresh token
            encrypted_refresh = cred.encrypted_refresh_token

        if cred:
            cred.scopes = canonical_scopes
            cred.encrypted_access_token = encrypted_access
            cred.encrypted_refresh_token = encrypted_refresh
            cred.token_expiry = token_expiry
            cred.status = "connected"
            cred.is_read_only = False
            cred.jobs_paused = False
            cred.error_message = None
            cred.updated_at = datetime.now(UTC)
        else:
            cred = ConnectorCredential(
                id=cred_id,
                tenant_id=t_id,
                provider=clean_provider,
                account_email=clean_email,
                scopes=canonical_scopes,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                token_expiry=token_expiry,
                status="connected",
                is_read_only=False,
                jobs_paused=False,
                error_message=None,
            )
            session.add(cred)

        await session.flush()

        # 5. Immutable Audit Log
        await write_audit(
            session,
            action="connector.connected",
            target_type="connector_credential",
            target_id=cred.id,
            details={
                "provider": clean_provider,
                "account_email": clean_email,
                "scopes": canonical_scopes,
                "status": "connected",
            },
            actor_id=actor_id,
            tenant_id=t_id,
        )

        return cred

    @staticmethod
    async def get_credential_by_id(
        session: AsyncSession,
        tenant_id: UUID | str,
        credential_id: UUID | str,
    ) -> ConnectorCredential:
        """Retrieves a connector credential by ID, enforcing tenant isolation."""
        t_id = UUID(str(tenant_id))
        c_id = UUID(str(credential_id))

        stmt = select(ConnectorCredential).where(
            ConnectorCredential.tenant_id == t_id,
            ConnectorCredential.id == c_id,
        )
        result = await session.execute(stmt)
        cred = result.scalar_one_or_none()
        if not cred:
            raise CredentialNotFoundError(f"Connector credential '{credential_id}' not found.")
        return cred

    @staticmethod
    async def get_active_credential(
        session: AsyncSession,
        tenant_id: UUID | str,
        provider: str = "google",
        account_email: str | None = None,
    ) -> ConnectorCredential:
        """Retrieves the active credential for a tenant and provider."""
        t_id = UUID(str(tenant_id))
        stmt = select(ConnectorCredential).where(
            ConnectorCredential.tenant_id == t_id,
            ConnectorCredential.provider == provider.strip().lower(),
            ConnectorCredential.status == "connected",
        )
        if account_email:
            stmt = stmt.where(ConnectorCredential.account_email == account_email.strip().lower())

        stmt = stmt.order_by(desc(ConnectorCredential.updated_at))
        result = await session.execute(stmt)
        cred = result.scalars().first()
        if not cred:
            raise CredentialNotFoundError(
                f"No active '{provider}' connector credential found for tenant."
            )
        return cred

    @staticmethod
    def get_decrypted_tokens(
        credential: ConnectorCredential,
    ) -> tuple[str, str | None]:
        """
        Decrypts access and refresh tokens using AES-256-GCM envelope decryption.
        """
        ctx_access = EncryptionContext(
            tenant_id=credential.tenant_id,
            entity_type="connector_credential",
            record_id=credential.id,
            field_name="access_token",
        )
        access_token = decrypt_field(credential.encrypted_access_token, ctx_access)

        refresh_token: str | None = None
        if credential.encrypted_refresh_token:
            ctx_refresh = EncryptionContext(
                tenant_id=credential.tenant_id,
                entity_type="connector_credential",
                record_id=credential.id,
                field_name="refresh_token",
            )
            refresh_token = decrypt_field(credential.encrypted_refresh_token, ctx_refresh)

        return access_token, refresh_token

    @staticmethod
    async def ensure_valid_access_token(
        session: AsyncSession,
        credential: ConnectorCredential,
    ) -> str:
        """
        Ensures the credential has a valid, non-expired access token.
        If token_expiry is not set, or is within a 5-minute safety buffer
        (now + 5 minutes >= token_expiry), automatically refreshes the token.
        Otherwise, returns the decrypted existing access token.
        If refresh fails (e.g. invalid_grant / 401), trips the circuit breaker
        and raises ConnectorAuthExpiredError.
        """
        now = datetime.now(UTC)
        buffer = timedelta(minutes=5)

        # Check if expired or within buffer
        needs_refresh = False
        if credential.token_expiry is None or credential.token_expiry <= (now + buffer):
            needs_refresh = True

        if not needs_refresh:
            access_token, _ = ConnectorService.get_decrypted_tokens(credential)
            return access_token

        logger.info(
            f"[TokenRefresh] Access token for credential '{credential.id}' "
            f"expired or near expiry (expiry={credential.token_expiry}). Refreshing..."
        )
        return await ConnectorService.refresh_access_token(session, credential)

    @staticmethod
    async def refresh_access_token(
        session: AsyncSession,
        credential: ConnectorCredential,
        actor_id: UUID | str | None = None,
    ) -> str:
        """
        Refreshes an OAuth access token using the stored encrypted refresh token.
        Re-encrypts the new access token under AES-256-GCM envelope encryption with AAD,
        updates token_expiry, records audit log, and returns the new plaintext access token.
        If the refresh token is invalid/revoked (invalid_grant), trips the circuit breaker to
        needs_reconnect and pauses jobs.
        """
        _, refresh_token = ConnectorService.get_decrypted_tokens(credential)
        if not refresh_token:
            err_msg = "No refresh token available to refresh expired access token."
            await ConnectorService.handle_auth_failure(
                session,
                tenant_id=credential.tenant_id,
                credential_id=credential.id,
                error_detail=err_msg,
            )
            raise ConnectorAuthExpiredError(err_msg)

        new_access_token: str
        new_expiry: datetime

        if settings.USE_FAKE_INTEGRATIONS:
            # Fake / Sandbox token refresh logic
            if "invalid_grant" in refresh_token.lower() or "revoked" in refresh_token.lower():
                err_msg = "OAuth token refresh failed: invalid_grant (token revoked or invalid)."
                await ConnectorService.handle_auth_failure(
                    session,
                    tenant_id=credential.tenant_id,
                    credential_id=credential.id,
                    error_detail=err_msg,
                )
                raise ConnectorAuthExpiredError(err_msg)

            new_access_token = f"fake_refreshed_access_{uuid.uuid4().hex[:12]}"
            new_expiry = datetime.now(UTC) + timedelta(hours=1)
        else:
            # Real Google OAuth Token Refresh
            token_url = "https://oauth2.googleapis.com/token"
            payload = {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(token_url, data=payload)
            except Exception as net_err:
                raise ConnectorServiceUnavailableError(
                    f"Network error refreshing token: {net_err}"
                ) from net_err

            if resp.status_code in (400, 401) or "invalid_grant" in resp.text:
                err_msg = f"OAuth token refresh rejected by Google: {resp.text}"
                await ConnectorService.handle_auth_failure(
                    session,
                    tenant_id=credential.tenant_id,
                    credential_id=credential.id,
                    error_detail=err_msg,
                )
                raise ConnectorAuthExpiredError(err_msg)

            if resp.status_code != 200:
                raise ConnectorError(
                    f"Unexpected token refresh response ({resp.status_code}): {resp.text}"
                )

            token_data = resp.json()
            new_access_token = token_data["access_token"]
            expires_in = int(token_data.get("expires_in", 3600))
            new_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)

        # Re-encrypt new access token under AES-256-GCM envelope encryption with AAD
        ctx_access = EncryptionContext(
            tenant_id=credential.tenant_id,
            entity_type="connector_credential",
            record_id=credential.id,
            field_name="access_token",
        )
        encrypted_access = encrypt_field(new_access_token, ctx_access)

        credential.encrypted_access_token = encrypted_access
        credential.token_expiry = new_expiry
        credential.status = "connected"
        credential.jobs_paused = False
        credential.error_message = None
        credential.updated_at = datetime.now(UTC)

        await session.flush()

        # Immutable Audit Log
        await write_audit(
            session,
            action="connector.token_refreshed",
            target_type="connector_credential",
            target_id=credential.id,
            details={
                "provider": credential.provider,
                "account_email": credential.account_email,
                "token_expiry": new_expiry.isoformat(),
            },
            actor_id=actor_id,
            tenant_id=credential.tenant_id,
        )

        return new_access_token

    @staticmethod
    async def set_read_only(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        credential_id: UUID | str,
        is_read_only: bool,
        actor_id: UUID | str | None = None,
    ) -> ConnectorCredential:
        """Toggles read-only mode for a connector credential and emits audit log."""
        cred = await ConnectorService.get_credential_by_id(session, tenant_id, credential_id)
        cred.is_read_only = is_read_only
        cred.updated_at = datetime.now(UTC)
        await session.flush()

        await write_audit(
            session,
            action="connector.read_only_toggled",
            target_type="connector_credential",
            target_id=cred.id,
            details={"is_read_only": is_read_only, "provider": cred.provider},
            actor_id=actor_id,
            tenant_id=UUID(str(tenant_id)),
        )
        return cred

    @staticmethod
    async def handle_auth_failure(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        credential_id: UUID | str,
        error_detail: str,
    ) -> ConnectorCredential:
        """
        Transitions credential to needs_reconnect, pauses jobs, and writes audit record.
        Invoked automatically when a provider call or token refresh returns 401 or invalid_grant.
        """
        cred = await ConnectorService.get_credential_by_id(session, tenant_id, credential_id)
        cred.status = "needs_reconnect"
        cred.jobs_paused = True
        cred.error_message = error_detail
        cred.updated_at = datetime.now(UTC)
        await session.flush()

        await write_audit(
            session,
            action="connector.needs_reconnect",
            target_type="connector_credential",
            target_id=cred.id,
            details={
                "reason": error_detail,
                "jobs_paused": True,
                "provider": cred.provider,
                "account_email": cred.account_email,
            },
            tenant_id=UUID(str(tenant_id)),
        )
        return cred

    @staticmethod
    async def revoke_credential(
        session: AsyncSession,
        *,
        tenant_id: UUID | str,
        credential_id: UUID | str,
        actor_id: UUID | str | None = None,
    ) -> ConnectorCredential:
        """Marks credential as revoked and pauses jobs."""
        cred = await ConnectorService.get_credential_by_id(session, tenant_id, credential_id)
        cred.status = "revoked"
        cred.jobs_paused = True
        cred.updated_at = datetime.now(UTC)
        await session.flush()

        await write_audit(
            session,
            action="connector.revoked",
            target_type="connector_credential",
            target_id=cred.id,
            details={"provider": cred.provider, "account_email": cred.account_email},
            actor_id=actor_id,
            tenant_id=UUID(str(tenant_id)),
        )
        return cred

    @staticmethod
    async def list_credentials(
        session: AsyncSession,
        tenant_id: UUID | str,
    ) -> list[ConnectorCredential]:
        """Lists all credentials for a tenant."""
        t_id = UUID(str(tenant_id))
        stmt = (
            select(ConnectorCredential)
            .where(ConnectorCredential.tenant_id == t_id)
            .order_by(ConnectorCredential.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
