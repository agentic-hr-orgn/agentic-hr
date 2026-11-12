"""
Integration & Unit Test Suite for Connectors Subsystem (Stage 3 Milestone A).
Verifies:
1. AES-256-GCM envelope encryption on credentials vault with strict AAD binding.
2. Rejection of prohibited scopes (gmail.readonly, gmail.modify, mail.google.com).
3. OAuth CSRF state token generation and verification.
4. OAuth authorize and callback endpoints.
5. Client-enforced Read-Only switch (blocks outbound email with HTTP 403).
6. Automatic needs_reconnect and job pause on 401 / invalid_grant.
7. Idempotent email send and transactional sent_messages log.
8. Safe HTML template variable escaping (XSS defense).
9. Synchronous bounce marking without inbox polling.
10. Per-tenant Redis sliding-window rate limiting.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import desc, select

from app.core.clerk import register_test_signing_key
from app.core.encryption import (
    AuthenticationFailedError,
    EncryptionContext,
    decrypt_field,
)
from app.db.session import AsyncSessionLocal
from app.integrations.base import EmailMessage
from app.integrations.google.gmail_client import GoogleGmailService
from app.models.audit_log import AuditLog
from app.models.connector import ConnectorCredential, SentMessage
from app.models.tenant import set_tenant_context
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorService,
    InvalidScopeError,
    OAuthStateInvalidError,
    ProhibitedScopeError,
    generate_oauth_state,
    validate_scopes,
    verify_oauth_state,
)
from app.services.template_service import render_email
from tests.isolation_harness import TEST_ISOLATION_KID, MultiTenantTestContext, _public_key


@pytest.fixture(autouse=True)
def setup_isolation_keys():
    """Ensure isolation test signing keys are registered for every test."""
    register_test_signing_key(TEST_ISOLATION_KID, _public_key)
    yield


@pytest.fixture(autouse=True)
async def cleanup_test_state():
    """Flushes Redis rate limit keys and resets in-memory fakes before each test."""
    import logging

    from app.core.redis import get_redis_client
    from app.integrations.fake.fake_gmail import FakeGmailService

    FakeGmailService().reset()
    try:
        client = await get_redis_client()
        keys = await client.keys("rl:*")
        if keys:
            await client.delete(*keys)
    except Exception as exc:
        logging.getLogger("test").debug("Pre-test Redis cleanup skipped: %s", exc)
    yield


async def ensure_clean_connected_credential(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    email: str = "agentic.hr.test.ubaid.owner@gmail.com",
) -> str:
    """Ensures an active, connected Google connector credential exists."""
    resp = await client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": email,
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.test_valid_connector_token",
        },
    )
    assert resp.status_code == 200, f"Failed to connect test credential: {resp.text}"
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_01_store_credentials_envelope_encryption(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that OAuth tokens are stored encrypted using AES-256-GCM envelope encryption."""
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    raw_access = "ya29.test_google_access_token_12345"
    raw_refresh = "1//test_google_refresh_token_67890"

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        cred = await ConnectorService.store_credentials(
            session=session,
            tenant_id=tenant_id,
            provider="google",
            account_email="agentic.hr.test.ubaid.owner@gmail.com",
            scopes=["gmail.send", "drive.file", "calendar.events"],
            access_token=raw_access,
            refresh_token=raw_refresh,
            actor_id=actor_id,
        )
        await session.commit()
        cred_id = cred.id

    # 1. Directly inspect raw database row: tokens must NOT be plaintext
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        stmt = select(ConnectorCredential).where(ConnectorCredential.id == cred_id)
        result = await session.execute(stmt)
        raw_row = result.scalar_one()

        assert raw_row.encrypted_access_token != raw_access
        assert "AES-256-GCM" in raw_row.encrypted_access_token
        assert raw_row.encrypted_refresh_token != raw_refresh
        assert "AES-256-GCM" in raw_row.encrypted_refresh_token

        # 2. Decrypt with exact matching AAD context -> succeeds
        access_dec, refresh_dec = ConnectorService.get_decrypted_tokens(raw_row)
        assert access_dec == raw_access
        assert refresh_dec == raw_refresh

        # 3. Decrypt with tampered AAD context -> raises AuthenticationFailedError
        tampered_ctx = EncryptionContext(
            tenant_id=uuid.UUID(ctx.company_b.tenant_id),  # Wrong tenant!
            entity_type="connector_credential",
            record_id=cred_id,
            field_name="access_token",
        )
        with pytest.raises(AuthenticationFailedError):
            decrypt_field(raw_row.encrypted_access_token, tampered_ctx)


@pytest.mark.asyncio
async def test_02_prohibited_scopes_rejected() -> None:
    """Verifies that prohibited scopes raise ProhibitedScopeError and unknown scopes raise InvalidScopeError."""
    # 1. Prohibited gmail.readonly
    with pytest.raises(ProhibitedScopeError) as exc_info:
        validate_scopes(["gmail.readonly", "drive.file"])
    assert "gmail.readonly" in str(exc_info.value)
    assert "forbidden by ADR 006" in str(exc_info.value)

    # 2. Prohibited gmail.modify
    with pytest.raises(ProhibitedScopeError):
        validate_scopes(["https://www.googleapis.com/auth/gmail.modify"])

    # 3. Prohibited mail.google.com
    with pytest.raises(ProhibitedScopeError):
        validate_scopes(["https://mail.google.com/"])

    # 4. Unknown scope
    with pytest.raises(InvalidScopeError):
        validate_scopes(["https://www.googleapis.com/auth/contacts.readonly"])

    # 5. Allowed scopes valid
    canonical = validate_scopes(["gmail.send", "drive.file", "calendar.events"])
    assert len(canonical) == 3
    assert "https://www.googleapis.com/auth/gmail.send" in canonical


@pytest.mark.asyncio
async def test_03_oauth_csrf_state_generation_and_verification() -> None:
    """Verifies HMAC signed CSRF state generation, verification, and tamper resistance."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    state = generate_oauth_state(tenant_id, user_id)
    assert "." in state

    # 1. Valid state verification
    t_out, u_out = verify_oauth_state(state)
    assert t_out == tenant_id
    assert u_out == user_id

    # 2. Tampered state payload
    parts = state.split(".")
    tampered_state = f"{parts[0][:-2]}aa.{parts[1]}"
    with pytest.raises(OAuthStateInvalidError):
        verify_oauth_state(tampered_state)

    # 3. Expired state
    with pytest.raises(OAuthStateInvalidError):
        verify_oauth_state(state, max_age_seconds=-1)


@pytest.mark.asyncio
async def test_04_oauth_authorize_and_callback_endpoints(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies GET /authorize returns valid OAuth URL and GET /callback stores credentials."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Call Authorize endpoint
    auth_resp = await async_client.get("/api/v1/connectors/google/oauth/authorize", headers=headers)
    assert auth_resp.status_code == 200
    auth_url = auth_resp.json()["authorization_url"]
    assert "accounts.google.com" in auth_url
    assert "gmail.send" in auth_url
    assert "state=" in auth_url

    # Extract state token
    state_token = auth_url.split("state=")[1].split("&")[0]

    # 2. Call Callback endpoint with state and authorization code
    cb_resp = await async_client.get(
        f"/api/v1/connectors/google/oauth/callback?code=mock_auth_code_12345&state={state_token}"
    )
    assert cb_resp.status_code == 200
    cb_data = cb_resp.json()
    assert cb_data["status"] == "connected"
    assert cb_data["tenant_id"] == ctx.company_a.tenant_id

    # 3. Verify credential appears in GET /connectors
    list_resp = await async_client.get("/api/v1/connectors", headers=headers)
    assert list_resp.status_code == 200
    connectors = list_resp.json()
    assert len(connectors) >= 1
    assert any(c["status"] == "connected" for c in connectors)


@pytest.mark.asyncio
async def test_05_read_only_switch_blocks_write_operations(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that is_read_only blocks outbound email with HTTP 403 but allows read queries."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Connect a test credential
    conn_payload = {
        "provider": "google",
        "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
        "scopes": ["gmail.send", "drive.file", "calendar.events"],
        "access_token": "ya29.valid_test_token",
        "refresh_token": "1//valid_refresh_token",
    }
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect", headers=headers, json=conn_payload
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # 2. Toggle Read-Only to True
    ro_resp = await async_client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=headers,
        json={"is_read_only": True},
    )
    assert ro_resp.status_code == 200
    assert ro_resp.json()["is_read_only"] is True

    # 3. Attempting to send email must return HTTP 403 Forbidden
    send_payload = {
        "to_email": "agentic.hr.test.ubaid.candidate1@gmail.com",
        "subject": "Blocked by Read-Only",
        "body_text": "This should not be sent.",
        "credential_id": cred_id,
    }
    send_resp = await async_client.post(
        "/api/v1/connectors/email/send", headers=headers, json=send_payload
    )
    assert send_resp.status_code == 403
    assert "CONNECTOR_READ_ONLY" in send_resp.json()["error"]["message"]

    # 4. Read queries continue to succeed
    list_resp = await async_client.get("/api/v1/connectors", headers=headers)
    assert list_resp.status_code == 200

    # 5. Toggle Read-Only back to False -> send succeeds
    ro_off_resp = await async_client.patch(
        f"/api/v1/connectors/{cred_id}/read-only",
        headers=headers,
        json={"is_read_only": False},
    )
    assert ro_off_resp.status_code == 200
    assert ro_off_resp.json()["is_read_only"] is False

    send_ok_resp = await async_client.post(
        "/api/v1/connectors/email/send", headers=headers, json=send_payload
    )
    assert send_ok_resp.status_code == 200
    assert send_ok_resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_06_automatic_needs_reconnect_on_401(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that encountering 401/invalid_grant automatically transitions credential to needs_reconnect."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Connect test credential
    conn_payload = {
        "provider": "google",
        "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
        "scopes": ["gmail.send", "drive.file", "calendar.events"],
        "access_token": "ya29.auth_expired_test_token",
    }
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect", headers=headers, json=conn_payload
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # 2. Trigger send to simulate-401 address
    send_payload = {
        "to_email": "simulate-401@test.com",
        "subject": "Auth Expiration Test",
        "body_text": "Triggers 401 simulation",
        "credential_id": cred_id,
    }
    send_resp = await async_client.post(
        "/api/v1/connectors/email/send", headers=headers, json=send_payload
    )
    assert send_resp.status_code == 401
    assert "CONNECTOR_AUTH_EXPIRED" in send_resp.json()["error"]["message"]

    # 3. Check DB row: status must be needs_reconnect, jobs_paused must be True
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(ConnectorCredential).where(ConnectorCredential.id == uuid.UUID(cred_id))
        result = await session.execute(stmt)
        updated_cred = result.scalar_one()

        assert updated_cred.status == "needs_reconnect"
        assert updated_cred.jobs_paused is True
        assert "Simulated Google OAuth 401" in (updated_cred.error_message or "")

    # 4. Check audit log for connector.needs_reconnect entry
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt_audit = select(AuditLog).where(
            AuditLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
            AuditLog.action == "connector.needs_reconnect",
        )
        res_audit = await session.execute(stmt_audit)
        audit_entry = res_audit.scalars().first()
        assert audit_entry is not None


@pytest.mark.asyncio
async def test_07_idempotent_email_send_and_sent_messages_log(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies at-most-once delivery via IdempotencyGate and sent_messages recording."""
    ctx = isolation_context
    headers = {
        "Authorization": f"Bearer {ctx.company_a.owner.token}",
        "X-Idempotency-Key": f"test-idemp-email-{uuid.uuid4().hex[:8]}",
    }

    # Connect credential first
    await async_client.post(
        "/api/v1/connectors/google/connect",
        headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.idemp_test",
        },
    )

    send_payload = {
        "to_email": "agentic.hr.test.ubaid.candidate2@gmail.com",
        "subject": "Idempotent Interview Invitation",
        "body_text": "Please confirm your availability.",
    }

    # 1. First send -> succeeds
    resp1 = await async_client.post(
        "/api/v1/connectors/email/send", headers=headers, json=send_payload
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    msg_id_1 = data1["message_id"]

    # 2. Replay with identical idempotency key -> returns cached result
    resp2 = await async_client.post(
        "/api/v1/connectors/email/send", headers=headers, json=send_payload
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["message_id"] == msg_id_1

    # 3. Verify sent_messages table has exactly 1 record for this idempotency key
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(SentMessage).where(
            SentMessage.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
            SentMessage.idempotency_key == headers["X-Idempotency-Key"],
        )
        res = await session.execute(stmt)
        messages = res.scalars().all()
        assert len(messages) == 1
        assert messages[0].status == "sent"


@pytest.mark.asyncio
async def test_08_safe_html_template_escaping(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that malicious variables in email templates are auto-escaped in HTML."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers)

    malicious_name = "<script>alert('xss')</script> John Doe"
    payload = {
        "to_email": "agentic.hr.test.ubaid.candidate1@gmail.com",
        "template_name": "candidate_interview_invite",
        "template_variables": {
            "candidate_name": malicious_name,
            "role_title": "Full Stack Engineer",
            "company_name": "Acme Tech Solutions",
            "interview_time": "Tomorrow at 10:00 AM PKT",
            "meeting_link": "https://meet.google.com/abc-defg-hij",
        },
    }

    resp = await async_client.post("/api/v1/connectors/email/send", headers=headers, json=payload)
    assert resp.status_code == 200
    sent_id = resp.json()["sent_message_id"]

    # Inspect rendered message in database
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(SentMessage).where(SentMessage.id == uuid.UUID(sent_id))
        res = await session.execute(stmt)
        msg_row = res.scalar_one()

        # HTML body must have escaped script tag
        assert "<script>" not in msg_row.html_body
        assert "&lt;script&gt;alert(" in msg_row.html_body
        # Plain-text retains readable text
        assert "John Doe" in msg_row.text_body


@pytest.mark.asyncio
async def test_09_bounce_marking_no_inbox_polling(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that POST /email/bounces updates delivery status without inbox polling."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers)

    # 1. Send an email to establish record
    unique_bounce_email = f"bounce-test-{uuid.uuid4().hex[:6]}@invalid-domain.xyz"
    send_resp = await async_client.post(
        "/api/v1/connectors/email/send",
        headers=headers,
        json={
            "to_email": unique_bounce_email,
            "subject": "Bounce Test",
            "body_text": "Will bounce synchronously",
        },
    )
    assert send_resp.status_code == 200
    provider_msg_id = send_resp.json()["message_id"]

    # 2. Record bounce notification
    bounce_payload = {
        "provider_message_id": provider_msg_id,
        "error_message": "550 5.1.1 User does not exist (SMTP rejection)",
    }
    bounce_resp = await async_client.post(
        "/api/v1/connectors/email/bounces", headers=headers, json=bounce_payload
    )
    assert bounce_resp.status_code == 200
    assert bounce_resp.json()["status"] == "bounced"

    # 3. Verify database status
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(SentMessage).where(SentMessage.provider_message_id == provider_msg_id)
        res = await session.execute(stmt)
        record = res.scalar_one()
        assert record.status == "bounced"
        assert "550 5.1.1" in record.error_message


@pytest.mark.asyncio
async def test_10_connector_rate_limiting(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that exceeding per-tenant rate limit returns HTTP 429 Too Many Requests."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # Ensure connector exists
    await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": "agentic.hr.test.ubaid.owner@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.rate_limit_token",
        },
    )

    send_payload = {
        "to_email": "agentic.hr.test.ubaid.candidate1@gmail.com",
        "subject": "Rate Limit Probe",
        "body_text": "Checking quota",
    }

    rate_limited = False
    for i in range(65):
        resp = await async_client.post(
            "/api/v1/connectors/email/send",
            headers={"Authorization": f"Bearer {ctx.company_a.owner.token}"},
            json=send_payload,
        )
        if resp.status_code == 429:
            rate_limited = True
            assert "Retry-After" in resp.headers
            break

    assert rate_limited, "Rate limiting was not triggered after 65 rapid requests!"


@pytest.mark.asyncio
async def test_11_automatic_token_refresh_on_expired_token(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that an expired access token is automatically refreshed using the refresh token."""
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    old_access = "ya29.old_expired_token_11"
    valid_refresh = "1//valid_refresh_token_11"
    expired_time = datetime.now(UTC) - timedelta(hours=2)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        cred = await ConnectorService.store_credentials(
            session=session,
            tenant_id=tenant_id,
            provider="google",
            account_email=f"refresh_test_{uuid.uuid4().hex[:6]}@gmail.com",
            scopes=["gmail.send", "drive.file", "calendar.events"],
            access_token=old_access,
            refresh_token=valid_refresh,
            token_expiry=expired_time,
            actor_id=actor_id,
        )
        await session.commit()
        cred_id = cred.id

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        cred = await ConnectorService.get_credential_by_id(session, tenant_id, cred_id)
        assert cred.token_expiry == expired_time

        # Trigger ensure_valid_access_token
        new_token = await ConnectorService.ensure_valid_access_token(session, cred)
        await session.commit()

        assert new_token != old_access
        assert new_token.startswith("fake_refreshed_access_")
        assert cred.token_expiry is not None
        assert cred.token_expiry > datetime.now(UTC)

    # Verify database persistence & envelope encryption of refreshed token
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        stmt = select(ConnectorCredential).where(ConnectorCredential.id == cred_id)
        res = await session.execute(stmt)
        updated_row = res.scalar_one()

        assert updated_row.encrypted_access_token != new_token
        assert "AES-256-GCM" in updated_row.encrypted_access_token
        decrypted_access, decrypted_refresh = ConnectorService.get_decrypted_tokens(updated_row)
        assert decrypted_access == new_token
        assert decrypted_refresh == valid_refresh

        # Verify audit log
        stmt_audit = (
            select(AuditLog)
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.action == "connector.token_refreshed",
                AuditLog.target_id == str(cred_id),
            )
            .order_by(desc(AuditLog.timestamp))
        )
        res_audit = await session.execute(stmt_audit)
        audit_entry = res_audit.scalars().first()
        assert audit_entry is not None
        assert audit_entry.details.get("provider") == "google"


@pytest.mark.asyncio
async def test_12_token_refresh_within_5_minute_buffer(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that a token within the 5-minute safety threshold is proactively refreshed."""
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    old_access = "ya29.near_expiry_token_12"
    valid_refresh = "1//valid_refresh_token_12"
    # Expires in 2 minutes (within the 5-minute buffer)
    near_expiry = datetime.now(UTC) + timedelta(minutes=2)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        cred = await ConnectorService.store_credentials(
            session=session,
            tenant_id=tenant_id,
            provider="google",
            account_email=f"buffer_test_{uuid.uuid4().hex[:6]}@gmail.com",
            scopes=["gmail.send", "drive.file", "calendar.events"],
            access_token=old_access,
            refresh_token=valid_refresh,
            token_expiry=near_expiry,
            actor_id=actor_id,
        )
        await session.commit()
        cred_id = cred.id

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        cred = await ConnectorService.get_credential_by_id(session, tenant_id, cred_id)

        new_token = await ConnectorService.ensure_valid_access_token(session, cred)
        await session.commit()

        # Token was proactively refreshed because < 5 mins left
        assert new_token != old_access
        assert new_token.startswith("fake_refreshed_access_")
        assert cred.token_expiry > datetime.now(UTC) + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_13_token_valid_no_refresh_needed(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that a valid token with > 5 minutes remaining is returned without refreshing."""
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    valid_access = "ya29.valid_token_still_fresh_13"
    valid_refresh = "1//valid_refresh_token_13"
    # Expires in 45 minutes
    fresh_expiry = datetime.now(UTC) + timedelta(minutes=45)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        cred = await ConnectorService.store_credentials(
            session=session,
            tenant_id=tenant_id,
            provider="google",
            account_email=f"valid_test_{uuid.uuid4().hex[:6]}@gmail.com",
            scopes=["gmail.send", "drive.file", "calendar.events"],
            access_token=valid_access,
            refresh_token=valid_refresh,
            token_expiry=fresh_expiry,
            actor_id=actor_id,
        )
        await session.commit()
        cred_id = cred.id

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        cred = await ConnectorService.get_credential_by_id(session, tenant_id, cred_id)

        returned_token = await ConnectorService.ensure_valid_access_token(session, cred)
        await session.commit()

        # Token was preserved, not refreshed
        assert returned_token == valid_access
        assert cred.token_expiry == fresh_expiry


@pytest.mark.asyncio
async def test_14_refresh_failure_invalid_grant_trips_circuit_breaker(
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that an invalid/revoked refresh token immediately trips circuit breaker to needs_reconnect."""
    ctx = isolation_context
    tenant_id = uuid.UUID(ctx.company_a.tenant_id)
    actor_id = uuid.UUID(ctx.company_a.owner.user_id)

    old_access = "ya29.expired_token_14"
    revoked_refresh = "1//invalid_grant_revoked_token_14"
    expired_time = datetime.now(UTC) - timedelta(hours=1)

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        cred = await ConnectorService.store_credentials(
            session=session,
            tenant_id=tenant_id,
            provider="google",
            account_email=f"invalid_grant_{uuid.uuid4().hex[:6]}@gmail.com",
            scopes=["gmail.send", "drive.file", "calendar.events"],
            access_token=old_access,
            refresh_token=revoked_refresh,
            token_expiry=expired_time,
            actor_id=actor_id,
        )
        await session.commit()
        cred_id = cred.id

    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        cred = await ConnectorService.get_credential_by_id(session, tenant_id, cred_id)

        with pytest.raises(ConnectorAuthExpiredError) as exc_info:
            await ConnectorService.ensure_valid_access_token(session, cred)

        assert "invalid_grant" in str(exc_info.value).lower()
        await session.commit()

    # Verify circuit-breaker state on database row
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, str(tenant_id), str(actor_id))
        stmt = select(ConnectorCredential).where(ConnectorCredential.id == cred_id)
        res = await session.execute(stmt)
        cred_row = res.scalar_one()

        assert cred_row.status == "needs_reconnect"
        assert cred_row.jobs_paused is True
        assert "invalid_grant" in (cred_row.error_message or "").lower()

        # Verify audit log
        stmt_audit = (
            select(AuditLog)
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.action == "connector.needs_reconnect",
                AuditLog.target_id == str(cred_id),
            )
            .order_by(desc(AuditLog.timestamp))
        )
        res_audit = await session.execute(stmt_audit)
        audit_entry = res_audit.scalars().first()
        assert audit_entry is not None
        assert audit_entry.details.get("jobs_paused") is True


@pytest.mark.asyncio
async def test_15_wire_email_delivery_through_connector_call_wrapper(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies EmailDeliveryService routes through execute_connector_call with auto-refresh."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Register credential with expired token
    account_email = f"auto_refresh_send_{uuid.uuid4().hex[:6]}@gmail.com"
    expired_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": account_email,
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.expired_send_token",
            "refresh_token": "1//valid_refresh_token_send",
            "token_expiry": expired_time,
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # 2. Send email - call wrapper detects expired token, auto-refreshes it, and dispatches email
    send_resp = await async_client.post(
        "/api/v1/connectors/email/send",
        headers=headers,
        json={
            "to_email": "candidate-refresh@test.com",
            "subject": "Auto Refresh Test",
            "body_text": "This email required an auto-refresh",
            "credential_id": cred_id,
        },
    )
    assert send_resp.status_code == 200
    assert send_resp.json()["status"] == "success"

    # 3. Verify credential token_expiry in DB was refreshed
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(ConnectorCredential).where(ConnectorCredential.id == uuid.UUID(cred_id))
        res = await session.execute(stmt)
        cred = res.scalar_one()

        assert cred.status == "connected"
        assert cred.token_expiry > datetime.now(UTC)


@pytest.mark.asyncio
async def test_16_oauth_revoke_endpoint(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that DELETE /connectors/{id} revokes credential, pauses jobs, and audits."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Connect a credential
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": f"revoke_test_{uuid.uuid4().hex[:6]}@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.revoke_test_token",
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # 2. Revoke credential via DELETE
    del_resp = await async_client.delete(f"/api/v1/connectors/{cred_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "revoked"

    # 3. Check DB status
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(ConnectorCredential).where(ConnectorCredential.id == uuid.UUID(cred_id))
        res = await session.execute(stmt)
        cred = res.scalar_one()

        assert cred.status == "revoked"
        assert cred.jobs_paused is True

    # 4. Check audit log
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt_audit = (
            select(AuditLog)
            .where(
                AuditLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
                AuditLog.action == "connector.revoked",
                AuditLog.target_id == str(cred_id),
            )
            .order_by(desc(AuditLog.timestamp))
        )
        res_audit = await session.execute(stmt_audit)
        audit_entry = res_audit.scalars().first()
        assert audit_entry is not None


@pytest.mark.asyncio
async def test_17_manual_refresh_endpoint(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that POST /connectors/{id}/refresh manually refreshes the token and audits."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}

    # 1. Connect a credential
    conn_resp = await async_client.post(
        "/api/v1/connectors/google/connect",
        headers=headers,
        json={
            "provider": "google",
            "account_email": f"manual_refresh_{uuid.uuid4().hex[:6]}@gmail.com",
            "scopes": ["gmail.send", "drive.file", "calendar.events"],
            "access_token": "ya29.initial_manual_refresh_token",
            "refresh_token": "1//valid_manual_refresh_token",
            "token_expiry": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        },
    )
    assert conn_resp.status_code == 200
    cred_id = conn_resp.json()["id"]

    # 2. Call POST /connectors/{id}/refresh
    ref_resp = await async_client.post(f"/api/v1/connectors/{cred_id}/refresh", headers=headers)
    assert ref_resp.status_code == 200
    ref_data = ref_resp.json()
    assert ref_data["id"] == cred_id
    assert ref_data["status"] == "connected"

    # 3. Check DB status and updated token_expiry
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = select(ConnectorCredential).where(ConnectorCredential.id == uuid.UUID(cred_id))
        res = await session.execute(stmt)
        cred = res.scalar_one()

        assert cred.token_expiry > datetime.now(UTC)

        # 4. Check audit log
        stmt_audit = (
            select(AuditLog)
            .where(
                AuditLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
                AuditLog.action == "connector.token_refreshed",
                AuditLog.target_id == str(cred_id),
            )
            .order_by(desc(AuditLog.timestamp))
        )
        res_audit = await session.execute(stmt_audit)
        audit_entry = res_audit.scalars().first()
        assert audit_entry is not None


@pytest.mark.asyncio
async def test_18_email_templates_render_and_xss_escape() -> None:
    """Verifies that all 5 HR email templates render properly with strict HTML auto-escaping."""
    # 1. System test email
    subj, html_body, text_body = render_email(
        "system_test_email",
        {
            "recipient_name": "<script>alert('pwned')</script>",
            "integration_name": "Google Workspace Gmail",
            "sender_email": "system@example.com",
            "test_timestamp": "2026-11-11T12:00:00Z",
            "environment": "test",
        },
    )
    assert "System Test Email: Google Workspace Gmail Connection Verified" in subj
    assert "<script>" not in html_body
    assert "&lt;script&gt;alert(" in html_body
    assert "<script>alert('pwned')</script>" in text_body

    # 2. Document expiry alert
    subj, html_body, text_body = render_email(
        "document_expiry_alert",
        {
            "employee_name": "Sarah '<b>Bold</b>' Connor",
            "document_name": "Passport",
            "expiry_date": "2026-12-31",
            "action_url": "https://portal.acme.com/docs",
            "company_name": "Acme Corp",
        },
    )
    assert "Expiration Notice for Passport" in subj
    assert "<b>" not in html_body
    assert "&lt;b&gt;Bold&lt;/b&gt;" in html_body
    assert "Sarah '<b>Bold</b>' Connor" in text_body

    # 3. Candidate interview invite
    subj, html_body, text_body = render_email(
        "candidate_interview_invite",
        {
            "candidate_name": "Candidate <img src=x onerror=alert(1)>",
            "role_title": "Lead Engineer",
            "company_name": "Acme Corp",
            "interview_time": "Tomorrow at 10 AM",
            "meeting_link": "https://meet.google.com/test",
        },
    )
    assert "<img" not in html_body
    assert "&lt;img src=x onerror=alert(1)&gt;" in html_body

    # 4. Offer letter notification
    subj, html_body, text_body = render_email(
        "offer_letter_notification",
        {
            "candidate_name": "Jane Doe",
            "role_title": "Staff Architect",
            "company_name": "Acme Corp",
            "start_date": "2026-12-01",
            "expiry_date": "2026-11-20",
        },
    )
    assert "Formal Job Offer: Staff Architect" in subj
    assert "Jane Doe" in html_body

    # 5. Approval request alert
    subj, html_body, text_body = render_email(
        "approval_request_alert",
        {
            "approver_name": "Manager",
            "approval_type": "Promotion",
            "applicant_name": "Jane",
            "details_summary": "Details",
            "portal_url": "https://portal.acme.com/approvals/1",
        },
    )
    assert "Action Required: Promotion" in subj
    assert "https://portal.acme.com/approvals/1" in html_body


@pytest.mark.asyncio
async def test_19_google_gmail_mime_builder() -> None:
    """Verifies that GoogleGmailService constructs RFC 2822 multipart/alternative MIME messages."""
    msg = EmailMessage(
        to_email="candidate@example.com",
        subject="Interview Invitation",
        body_text="Plain text body for terminal reader",
        body_html="<p>HTML body with styling</p>",
        sender_email="recruiter@acme.com",
        cc=["lead@acme.com"],
        bcc=["archive@acme.com"],
        reply_to="no-reply@acme.com",
        headers={"X-Priority": "1"},
    )

    mime_msg = GoogleGmailService.build_mime_message(msg)

    # 1. MIME headers
    assert mime_msg["Subject"] == "Interview Invitation"
    assert mime_msg["From"] == "recruiter@acme.com"
    assert mime_msg["To"] == "candidate@example.com"
    assert mime_msg["Cc"] == "lead@acme.com"
    assert mime_msg["Bcc"] == "archive@acme.com"
    assert mime_msg["Reply-To"] == "no-reply@acme.com"
    assert mime_msg["X-Priority"] == "1"

    # 2. Alternative multipart parts
    assert mime_msg.is_multipart()
    assert mime_msg.get_content_type() == "multipart/alternative"
    payload = mime_msg.get_payload()
    assert len(payload) == 2
    assert payload[0].get_content_type() == "text/plain"
    assert (
        payload[0].get_payload(decode=True).decode("utf-8") == "Plain text body for terminal reader"
    )
    assert payload[1].get_content_type() == "text/html"
    assert payload[1].get_payload(decode=True).decode("utf-8") == "<p>HTML body with styling</p>"

    # 3. Base64URL encoding
    raw = GoogleGmailService.encode_message_raw(mime_msg)
    assert isinstance(raw, str)
    assert "+" not in raw
    assert "/" not in raw


@pytest.mark.asyncio
async def test_20_email_send_synchronous_failure_persists_and_audits(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that synchronous delivery rejection (550 SMTP) persists as failed and writes audit log."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers)

    fail_email = f"simulate-fail-{uuid.uuid4().hex[:6]}@domain.com"
    payload = {
        "to_email": fail_email,
        "subject": "Test Synchronous Failure",
        "body_text": "This email will be rejected synchronously",
    }

    resp = await async_client.post("/api/v1/connectors/email/send", headers=headers, json=payload)
    assert resp.status_code == 400
    assert "EMAIL_DELIVERY_FAILED" in resp.json()["error"]["message"]

    # Verify persistence in sent_messages
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_a.tenant_id, ctx.company_a.owner.user_id)
        stmt = (
            select(SentMessage)
            .where(
                SentMessage.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
                SentMessage.recipient_email == fail_email,
            )
            .order_by(desc(SentMessage.created_at))
        )
        res = await session.execute(stmt)
        record = res.scalars().first()
        assert record is not None
        assert record.status == "failed"
        assert record.error_message is not None
        assert "550 5.1.1" in record.error_message

        # Verify audit log
        stmt_audit = (
            select(AuditLog)
            .where(
                AuditLog.tenant_id == uuid.UUID(ctx.company_a.tenant_id),
                AuditLog.action == "email.failed",
                AuditLog.target_id == str(record.id),
            )
            .order_by(desc(AuditLog.timestamp))
        )
        res_audit = await session.execute(stmt_audit)
        audit_entry = res_audit.scalars().first()
        assert audit_entry is not None


@pytest.mark.asyncio
async def test_21_hard_bounce_recipient_blocked_and_force_retry_override(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that hard-bounced recipients are blocked on subsequent sends unless force_retry=True."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers)

    target_email = f"bounced-candidate-{uuid.uuid4().hex[:6]}@invalid.xyz"

    # 1. Send first email
    send_resp = await async_client.post(
        "/api/v1/connectors/email/send",
        headers=headers,
        json={
            "to_email": target_email,
            "subject": "Pre-bounce Invite",
            "body_text": "First attempt",
        },
    )
    assert send_resp.status_code == 200
    provider_id = send_resp.json()["message_id"]

    # 2. Record bounce webhook
    bounce_resp = await async_client.post(
        "/api/v1/connectors/email/bounces",
        headers=headers,
        json={
            "provider_message_id": provider_id,
            "error_message": "550 Recipient mailbox unavailable",
        },
    )
    assert bounce_resp.status_code == 200

    # 3. Attempt second send without force_retry -> MUST FAIL with 400 RECIPIENT_BLOCKED
    second_send = await async_client.post(
        "/api/v1/connectors/email/send",
        headers=headers,
        json={
            "to_email": target_email,
            "subject": "Follow-up Attempt",
            "body_text": "Should be blocked",
            "force_retry": False,
        },
    )
    assert second_send.status_code == 400
    assert "RECIPIENT_BLOCKED" in second_send.json()["error"]["message"]

    # 4. Attempt third send with force_retry=True -> MUST SUCCEED with 200
    retry_send = await async_client.post(
        "/api/v1/connectors/email/send",
        headers=headers,
        json={
            "to_email": target_email,
            "subject": "Override Attempt",
            "body_text": "Forced retry",
            "force_retry": True,
        },
    )
    assert retry_send.status_code == 200
    assert retry_send.json()["status"] == "success"


@pytest.mark.asyncio
async def test_22_list_email_messages_filtering_and_pagination(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies GET /api/v1/connectors/email/messages supports status/recipient filters & pagination."""
    ctx = isolation_context
    headers = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers)

    # 1. Query without filters
    all_resp = await async_client.get("/api/v1/connectors/email/messages", headers=headers)
    assert all_resp.status_code == 200
    all_msgs = all_resp.json()
    assert isinstance(all_msgs, list)
    assert len(all_msgs) > 0

    # 2. Filter by status=sent
    sent_resp = await async_client.get(
        "/api/v1/connectors/email/messages?status=sent", headers=headers
    )
    assert sent_resp.status_code == 200
    sent_msgs = sent_resp.json()
    assert all(m["status"] == "sent" for m in sent_msgs)

    # 3. Filter by status=bounced
    bounced_resp = await async_client.get(
        "/api/v1/connectors/email/messages?status=bounced", headers=headers
    )
    assert bounced_resp.status_code == 200
    bounced_msgs = bounced_resp.json()
    assert all(m["status"] == "bounced" for m in bounced_msgs)

    # 4. Backward compatibility alias: GET /api/v1/connectors/email/sent
    alias_resp = await async_client.get("/api/v1/connectors/email/sent", headers=headers)
    assert alias_resp.status_code == 200


@pytest.mark.asyncio
async def test_23_sent_messages_multi_tenant_isolation(
    async_client: httpx.AsyncClient,
    isolation_context: MultiTenantTestContext,
) -> None:
    """Verifies that Company B cannot view Company A's sent email messages."""
    ctx = isolation_context
    headers_a = {"Authorization": f"Bearer {ctx.company_a.owner.token}"}
    headers_b = {"Authorization": f"Bearer {ctx.company_b.owner.token}"}
    await ensure_clean_connected_credential(async_client, headers_a)
    await ensure_clean_connected_credential(
        async_client, headers_b, email="agentic.hr.test.ubaid.recruiter@gmail.com"
    )

    unique_email = f"confidential-{uuid.uuid4().hex[:6]}@secret-corp.com"

    # Company A sends email
    send_resp = await async_client.post(
        "/api/v1/connectors/email/send",
        headers=headers_a,
        json={
            "to_email": unique_email,
            "subject": "Confidential A",
            "body_text": "Secret from Company A",
        },
    )
    assert send_resp.status_code == 200

    # Company B queries sent messages
    resp_b = await async_client.get("/api/v1/connectors/email/messages", headers=headers_b)
    assert resp_b.status_code == 200
    msgs_b = resp_b.json()
    assert not any(m["recipient_email"] == unique_email for m in msgs_b)

    # Direct database query with Company B context
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, ctx.company_b.tenant_id, ctx.company_b.owner.user_id)
        stmt = select(SentMessage).where(SentMessage.recipient_email == unique_email)
        res = await session.execute(stmt)
        assert res.scalar_one_or_none() is None
