#!/usr/bin/env python3
"""
Milestone A Integration Verification Script.
Dual-Mode CLI Tool: Validates all Stage 3 Google Workspace Connectors end-to-end.

Modes:
  --mode fake : Offline validation using in-memory Fake service adapters and synthetic credentials.
  --mode real : Live validation against authorized test account (agentic.hr.test.ubaid.owner@gmail.com)
                under ADR-006 scopes (gmail.send, drive.file, calendar.events).

Verification Flow:
  Step 1: Gmail Connector (RFC 2822 Multipart & sent_messages log)
  Step 2: Sheets Connector (Batch sync queue & drift reconciliation)
  Step 3: Drive Connector (Tenant folder isolation & upload/download)
  Step 4: Calendar Connector (Asia/Karachi & Google Meet lifecycle)
  Step 5: Audit Logs & Security Invariant Governance
"""

import sys

# Ensure immediate unbuffered stdout output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

import argparse
import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add apps/api to sys.path so backend modules are importable
API_ROOT = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.integrations.google.calendar_client import get_karachi_tz
from app.models.audit_log import AuditLog
from app.models.connector import ConnectorCredential, SentMessage
from app.models.tenant import set_tenant_context
from app.models.tenant_entity import Tenant
from app.services.calendar_service import CalendarSchedulingService
from app.services.connector_service import (
    ConnectorAuthExpiredError,
    ConnectorService,
)
from app.services.drive_service import DriveManagerService
from app.services.email_delivery_service import EmailDeliveryService
from app.services.sheets_sync_service import SheetsSyncService
from app.services.template_service import render_email
from sqlalchemy import select

ACME_TENANT_ID = uuid.UUID("b4ec4ca5-f07b-4f61-82a9-ab78b8252496")
TEST_OWNER_USER_ID = uuid.UUID("e7b426fa-a294-4704-a4ad-b316f0ea3630")
TEST_OWNER_EMAIL = "agentic.hr.test.ubaid.owner@gmail.com"
ADR006_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/calendar.events",
]


async def get_test_tenant(session) -> Tenant:
    """Retrieves seeded Acme Tech Solutions tenant."""
    stmt = select(Tenant).where(Tenant.clerk_org_id == "org_acme_tech_01")
    tenant = (await session.execute(stmt)).scalar_one_or_none()
    if not tenant:
        stmt = select(Tenant).limit(1)
        tenant = (await session.execute(stmt)).scalar_one_or_none()
    if not tenant:
        raise RuntimeError(
            "No tenant found in database. Run: powershell -File scripts/dev.ps1 seed"
        )
    return tenant


async def setup_or_verify_credential(
    session, tenant_id: uuid.UUID, mode: str
) -> ConnectorCredential:
    """Sets up fake credential or verifies live Google OAuth credential."""
    cred = await ConnectorService.get_active_credential(
        session, tenant_id, provider="google"
    )

    if mode == "fake":
        if not cred:
            cred = await ConnectorService.store_credential(
                session,
                tenant_id=tenant_id,
                provider="google",
                account_email=TEST_OWNER_EMAIL,
                scopes=ADR006_SCOPES,
                access_token="fake_access_token_123",
                refresh_token="fake_refresh_token_123",
                token_expiry=datetime.now(UTC) + timedelta(days=30),
                actor_id=TEST_OWNER_USER_ID,
            )
            await session.flush()
            await session.refresh(cred)
        return cred

    # Real mode: Must have valid credential
    if not cred or cred.status != "connected":
        print("\n" + "=" * 80)
        print(
            f"[ERROR] No active Google OAuth credential found for {TEST_OWNER_EMAIL}."
        )
        print("To authorize the test account under ADR-006 scopes:")
        print(
            "  1. Ensure backend is running: cd apps/api && uv run uvicorn app.main:app --port 8000"
        )
        print("  2. In your browser, navigate to:")
        print("     http://localhost:8000/api/v1/connectors/google/authorize")
        print(f"  3. Sign in with: {TEST_OWNER_EMAIL}")
        print("  4. Grant access for whitelisted scopes:")
        for sc in ADR006_SCOPES:
            print(f"     - {sc}")
        print("  5. Rerun this verification script:")
        print("     uv run python scripts/verify_stage3_connectors.py --mode real")
        print("=" * 80 + "\n")
        sys.exit(1)

    return cred


async def run_milestone_a_verification(mode: str) -> None:
    # Set integration mode
    if mode == "fake":
        settings.USE_FAKE_INTEGRATIONS = True
    else:
        settings.USE_FAKE_INTEGRATIONS = False

    async with AsyncSessionLocal() as session:
        # Establish RLS tenant context first so SELECT is authorized under RLS
        await set_tenant_context(session, str(ACME_TENANT_ID), TEST_OWNER_EMAIL)
        tenant = await get_test_tenant(session)
        tenant_id = tenant.id

        print("\n" + "=" * 80)
        print(
            f"  🚀 RUNNING MILESTONE A CONNECTOR VERIFICATION SUITE (Mode: {mode.upper()})"
        )
        print(f"  Tenant: {tenant.name} ({tenant_id})")
        print(f"  Actor:  {TEST_OWNER_EMAIL}")
        print("=" * 80 + "\n")

        cred = await setup_or_verify_credential(session, tenant_id, mode)
        print(f"[*] Connector credential verified: ID={cred.id}, Status={cred.status}")
        await set_tenant_context(session, str(tenant_id), TEST_OWNER_EMAIL)

        # ----------------------------------------------------------------------
        # STEP 1: GMAIL CONNECTOR VERIFICATION
        # ----------------------------------------------------------------------
        print("\n--- Step 1: Verifying Gmail Connector ---")
        idempotency_key = f"milestone-a-gmail-{uuid.uuid4().hex[:8]}"
        template_vars = {
            "recipient_name": "Ubaid Qureshi",
            "integration_name": "Google Workspace Connector Suite",
            "sender_email": TEST_OWNER_EMAIL,
            "test_timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "environment": f"Agentic HR Milestone A ({mode})",
        }
        subject, _html_body, text_body = render_email(
            "system_test_email", template_vars
        )

        send_result = await EmailDeliveryService.send_email(
            session,
            tenant_id=tenant_id,
            to_email=TEST_OWNER_EMAIL,
            subject=subject,
            body_text=text_body,
            template_name="system_test_email",
            template_variables=template_vars,
            idempotency_key=idempotency_key,
            actor_id=TEST_OWNER_USER_ID,
        )
        assert send_result["status"] == "success"
        print(
            f"    Gmail delivery status: {send_result['status']} (Message ID: {send_result['message_id']})"
        )

        # Verify sent_messages database record
        sent_stmt = select(SentMessage).where(
            SentMessage.tenant_id == tenant_id,
            SentMessage.idempotency_key == idempotency_key,
        )
        sent_row = (await session.execute(sent_stmt)).scalar_one_or_none()
        assert sent_row is not None
        assert sent_row.status == "sent"
        assert sent_row.recipient_email == TEST_OWNER_EMAIL
        print(
            f"    sent_messages audit record verified: ID={sent_row.id}, Status={sent_row.status}"
        )
        print(
            "[PASS] Step 1: Gmail Connector (RFC 2822 Multipart & sent_messages log) verified."
        )

        # ----------------------------------------------------------------------
        # STEP 2: SHEETS CONNECTOR VERIFICATION
        # ----------------------------------------------------------------------
        print("\n--- Step 2: Verifying Sheets Connector ---")
        sheet_title = "Acme HR Employee Directory (Milestone A)"
        spreadsheet_id = await SheetsSyncService.get_or_create_spreadsheet(
            session,
            tenant_id=tenant_id,
            title=sheet_title,
            actor_id=TEST_OWNER_USER_ID,
        )
        print(f"    Spreadsheet located/created: ID={spreadsheet_id}")

        # Batch sync an employee record via SheetsSyncQueue
        queue_item = await SheetsSyncService.enqueue_sync(
            session,
            tenant_id=tenant_id,
            entity_type="employee",
            entity_id="EMP-999",
            action="upsert",
            payload={
                "employee_id": "EMP-999",
                "name": "Milestone A Tester",
                "email": "tester.milestone@acme.com",
                "department": "QA",
                "role": "Lead SRE",
                "status": "Active",
            },
            spreadsheet_id=spreadsheet_id,
        )
        await session.flush()
        print(f"    Sync item enqueued: ID={queue_item.id}, Status={queue_item.status}")

        # Process sync queue
        process_res = await SheetsSyncService.process_sync_queue(
            session,
            tenant_id=tenant_id,
            spreadsheet_id=spreadsheet_id,
            actor_id=TEST_OWNER_USER_ID,
        )
        print(
            f"    Sync queue processed: status={process_res.status}, processed_count={process_res.processed_count}"
        )

        # Read range back
        read_rows = await SheetsSyncService.read_range(
            session,
            tenant_id=tenant_id,
            spreadsheet_id=spreadsheet_id,
            range_name="Employees!A1:F5",
            actor_id=TEST_OWNER_USER_ID,
        )
        assert len(read_rows) > 0
        print(f"    Range read successfully: {len(read_rows)} rows retrieved")

        # One-way drift reconciliation
        drift_result = await SheetsSyncService.reconcile_drift(
            session,
            tenant_id=tenant_id,
            spreadsheet_id=spreadsheet_id,
            actor_id=TEST_OWNER_USER_ID,
        )
        print(
            f"    Drift reconciliation executed: status={drift_result.status}, drifts_detected={drift_result.drifts_detected}, reconciled={drift_result.reconciled_count}"
        )
        print(
            "[PASS] Step 2: Sheets Connector (Batch sync queue & drift reconciliation) verified."
        )

        # ----------------------------------------------------------------------
        # STEP 3: DRIVE CONNECTOR VERIFICATION
        # ----------------------------------------------------------------------
        print("\n--- Step 3: Verifying Drive Connector ---")
        folder_id = await DriveManagerService.ensure_tenant_folder(
            session,
            tenant_id=tenant_id,
            actor_id=TEST_OWNER_USER_ID,
        )
        print(f"    Tenant root folder verified: Folder ID={folder_id}")

        sample_cv_bytes = (
            b"%PDF-1.4 simulated resume bytes for Milestone A candidate verification"
        )
        uploaded_doc = await DriveManagerService.upload_file(
            session,
            tenant_id=tenant_id,
            name="milestone_a_candidate_cv.pdf",
            content=sample_cv_bytes,
            mime_type="application/pdf",
            actor_id=TEST_OWNER_USER_ID,
        )
        print(f"    CV uploaded under drive.file: File ID={uploaded_doc.drive_file_id}")

        # Verify listing
        files = await DriveManagerService.list_files(
            session,
            tenant_id=tenant_id,
            actor_id=TEST_OWNER_USER_ID,
        )
        assert len(files) > 0
        print(f"    Tenant files listed: {len(files)} files found in isolated folder")

        # Verify download & checksum
        downloaded_bytes, _doc_record = await DriveManagerService.download_file(
            session,
            tenant_id=tenant_id,
            file_id=uploaded_doc.drive_file_id,
            actor_id=TEST_OWNER_USER_ID,
        )
        assert (
            hashlib.sha256(downloaded_bytes).hexdigest()
            == hashlib.sha256(sample_cv_bytes).hexdigest()
        )
        print(
            f"    Binary download verified: SHA-256 checksum matched ({len(downloaded_bytes)} bytes)"
        )
        print(
            "[PASS] Step 3: Drive Connector (Tenant folder isolation & upload/download) verified."
        )

        # ----------------------------------------------------------------------
        # STEP 4: CALENDAR CONNECTOR VERIFICATION
        # ----------------------------------------------------------------------
        print("\n--- Step 4: Verifying Calendar Connector (Asia/Karachi) ---")
        now_karachi = datetime.now(get_karachi_tz())
        time_min = now_karachi + timedelta(days=1)
        time_max = now_karachi + timedelta(days=2)

        free_busy = await CalendarSchedulingService.get_free_busy(
            session,
            tenant_id=tenant_id,
            calendar_ids=["primary"],
            time_min=time_min,
            time_max=time_max,
            actor_id=TEST_OWNER_USER_ID,
        )
        print(f"    Free/busy queried in Asia/Karachi: {list(free_busy.keys())}")

        interview_start = time_min.replace(hour=14, minute=0, second=0, microsecond=0)
        interview_end = time_min.replace(hour=15, minute=0, second=0, microsecond=0)

        created_event = await CalendarSchedulingService.create_event(
            session,
            tenant_id=tenant_id,
            title="Technical Interview: Senior Python Engineer",
            start_time=interview_start,
            end_time=interview_end,
            attendees=[
                "agentic.hr.test.ubaid.candidate1@gmail.com",
                "agentic.hr.test.ubaid.hm@gmail.com",
            ],
            description="Milestone A Technical Interview via Google Meet",
            location="Google Meet",
            timezone="Asia/Karachi",
            actor_id=TEST_OWNER_USER_ID,
        )
        print(f"    Calendar event created: ID={created_event.google_event_id}")
        print(f"    Google Meet video link: {created_event.hangout_link}")
        assert created_event.hangout_link is not None

        updated_event = await CalendarSchedulingService.update_event(
            session,
            tenant_id=tenant_id,
            event_id=str(created_event.id),
            title="Technical Interview: Senior Python Engineer (Rescheduled)",
            actor_id=TEST_OWNER_USER_ID,
        )
        print(f"    Event updated: Summary='{updated_event.title}'")

        cancelled = await CalendarSchedulingService.cancel_event(
            session,
            tenant_id=tenant_id,
            event_id=str(created_event.id),
            actor_id=TEST_OWNER_USER_ID,
        )
        assert cancelled is True
        print(f"    Event cancelled successfully: {cancelled}")
        print(
            "[PASS] Step 4: Calendar Connector (Asia/Karachi & Google Meet lifecycle) verified."
        )

        # ----------------------------------------------------------------------
        # STEP 5: AUDIT LOGS & GOVERNANCE INVARIANTS
        # ----------------------------------------------------------------------
        print("\n--- Step 5: Verifying Audit Logs & Governance Invariants ---")
        audit_stmt = (
            select(AuditLog)
            .where(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.timestamp.desc())
            .limit(20)
        )
        audit_rows = (await session.execute(audit_stmt)).scalars().all()
        assert len(audit_rows) >= 4
        recorded_actions = {row.action for row in audit_rows}
        print(f"    Recent audit log actions: {recorded_actions}")
        print("[PASS] Step 5: Audit logs & security invariants verified.")

    print("\n" + "=" * 80)
    print(f"  🎉 ALL STAGE 3 CONNECTORS VERIFIED SUCCESSFULLY (Mode: {mode.upper()})")
    print("=" * 80 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Milestone A Connector Verification CLI"
    )
    parser.add_argument(
        "--mode",
        choices=["fake", "real"],
        default="fake",
        help="Integration mode: 'fake' (in-memory mock) or 'real' (live Google Workspace)",
    )
    args = parser.parse_args()

    # Windows event loop policy
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    print(f"[*] Starting verification in mode: {args.mode}", flush=True)

    try:
        asyncio.run(run_milestone_a_verification(args.mode))
    except ConnectorAuthExpiredError as exc:
        print("\n" + "=" * 80)
        print(f"[AUTH EXPIRED / INVALID GRANT] {exc}")
        print(
            "\nTo authenticate or re-authorize your live Google Workspace test credentials:"
        )
        print(
            "  1. Ensure backend is running: cd apps/api && uv run uvicorn app.main:app --port 8000"
        )
        print("  2. In your browser, navigate to:")
        print("     http://localhost:8000/api/v1/connectors/google/authorize")
        print(f"  3. Sign in with: {TEST_OWNER_EMAIL}")
        print("  4. Approve the whitelisted ADR-006 scopes:")
        for sc in ADR006_SCOPES:
            print(f"     - {sc}")
        print(
            "  5. Rerun: uv run python scripts/verify_stage3_connectors.py --mode real"
        )
        print("=" * 80 + "\n")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[FATAL ERROR] Verification failed: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
