"""
Stage 7 Prompt 1 Verification Suite:
- Migration 0018 tables (`chat_sessions`, `tickets`, `chat_messages`, `answer_feedback`,
  `ticket_messages`, `ticket_assignment_rules`), Forced RLS, `agentic_hr_migrator` ownership,
  revoked `TRUNCATE`, and 6-point cross-tenant isolation under `agentic_hr_app`
- Non-breaking `policy_documents.doc_scope` extension (`hr_policy` | `helpdesk_kb`)
- Unique & CHECK constraints across Stage 7 tables (including `'roman_ur'` language support)
- Secure view `public.pub_ticket_summary` (`security_invoker=true`, `security_barrier=true`),
  non-null `public_message_count`, and strict exclusion of internal notes
- Pydantic validation for Stage 7 chat, citation, and ticket contracts
"""

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from pydantic import ValidationError

from app.schemas.chat import CitationItem, PolicyBrainAnswer
from app.schemas.ticket import TicketAssignmentRuleUpsertRequest, TicketCloseRequest
from tests.isolation_harness import (
    MIGRATION_DB_URL,
    MultiTenantTestContext,
    assert_table_tenant_isolation,
    get_app_connection,
)

STAGE7_TABLES: tuple[str, ...] = (
    "chat_sessions",
    "tickets",
    "chat_messages",
    "answer_feedback",
    "ticket_messages",
    "ticket_assignment_rules",
)


# ==============================================================================
# 1. Migration 0018 Schema, Forced RLS & 6-Point Cross-Tenant Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_01_stage7_tables_forced_rls_and_cross_tenant_isolation(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies all 6 Stage 7 tables have Forced RLS, `agentic_hr_migrator` ownership,
    revoked `TRUNCATE`, and 6-point cross-tenant isolation under `agentic_hr_app`,
    plus `policy_documents.doc_scope` constraint enforcement.
    """
    acme = isolation_context.company_a
    globex = isolation_context.company_b

    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)
    created_ids: dict[str, tuple[str, str]] = {}

    try:
        # 1. Verify Forced RLS & ownership metadata
        meta_rows = await admin_conn.fetch(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) AS owner
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY($1::text[]);
            """,
            list(STAGE7_TABLES),
        )
        meta_by_name = {r["relname"]: r for r in meta_rows}
        assert set(meta_by_name.keys()) == set(STAGE7_TABLES)
        for tbl in STAGE7_TABLES:
            row = meta_by_name[tbl]
            assert row["relrowsecurity"] is True, f"{tbl} missing ENABLE ROW LEVEL SECURITY"
            assert row["relforcerowsecurity"] is True, f"{tbl} missing FORCE ROW LEVEL SECURITY"
            assert row["owner"] == "agentic_hr_migrator", f"{tbl} wrong owner: {row['owner']}"

        # 2. Verify policy_documents.doc_scope column & CHECK constraint
        doc_scope_col = await admin_conn.fetchrow(
            """
            SELECT column_name, column_default, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'policy_documents'
              AND column_name = 'doc_scope';
            """
        )
        assert doc_scope_col is not None, "policy_documents.doc_scope missing"
        assert doc_scope_col["is_nullable"] == "NO"
        assert "hr_policy" in (doc_scope_col["column_default"] or "")

        # Verify visibility column was preserved intact
        vis_col = await admin_conn.fetchrow(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'policy_documents'
              AND column_name = 'visibility';
            """
        )
        assert vis_col is not None, "policy_documents.visibility must remain preserved"

        # 3. Seed isolated rows in Acme & Globex for all 6 tables
        now = datetime.now(UTC)
        reminder_at = now + timedelta(hours=20)
        due_at = now + timedelta(hours=24)

        sess_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.chat_sessions (tenant_id, user_id, title, language)
                VALUES ($1::uuid, $2::uuid, 'Acme Leave Inquiry', 'en')
                RETURNING id;
                """,
                acme.tenant_id,
                acme.employee.user_id,
            )
        )
        sess_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.chat_sessions (tenant_id, user_id, title, language)
                VALUES ($1::uuid, $2::uuid, 'Globex Policy Inquiry', 'roman_ur')
                RETURNING id;
                """,
                globex.tenant_id,
                globex.employee.user_id,
            )
        )
        created_ids["chat_sessions"] = (sess_a, sess_b)

        tkt_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.tickets (
                    tenant_id, ticket_number, requester_user_id, assigned_to_user_id,
                    category, priority, status, subject, description, source,
                    source_session_id, sla_due_at, sla_reminder_at
                )
                VALUES (
                    $1::uuid, $2, $3::uuid, $4::uuid,
                    'hr_policy', 'medium', 'open', 'Acme Policy Question',
                    'Need clarification on annual carry-forward.', 'chat_ask_hr',
                    $5::uuid, $6, $7
                )
                RETURNING id;
                """,
                acme.tenant_id,
                f"TKT-S7A-{uuid.uuid4().hex[:6]}",
                acme.employee.user_id,
                acme.owner.user_id,
                sess_a,
                due_at,
                reminder_at,
            )
        )
        tkt_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.tickets (
                    tenant_id, ticket_number, requester_user_id, assigned_to_user_id,
                    category, priority, status, subject, description, source,
                    source_session_id, sla_due_at, sla_reminder_at
                )
                VALUES (
                    $1::uuid, $2, $3::uuid, $4::uuid,
                    'it_access', 'high', 'open', 'Globex VPN Access',
                    'Need VPN credentials.', 'helpdesk_agent',
                    $5::uuid, $6, $7
                )
                RETURNING id;
                """,
                globex.tenant_id,
                f"TKT-S7B-{uuid.uuid4().hex[:6]}",
                globex.employee.user_id,
                globex.owner.user_id,
                sess_b,
                due_at,
                reminder_at,
            )
        )
        created_ids["tickets"] = (tkt_a, tkt_b)

        msg_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.chat_messages (
                    tenant_id, session_id, role, content, language, intent, ticket_id
                )
                VALUES ($1::uuid, $2::uuid, 'assistant', 'Acme answer', 'en', 'policy_qa', $3::uuid)
                RETURNING id;
                """,
                acme.tenant_id,
                sess_a,
                tkt_a,
            )
        )
        msg_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.chat_messages (
                    tenant_id, session_id, role, content, language, intent, ticket_id
                )
                VALUES ($1::uuid, $2::uuid, 'assistant', 'Globex jawab', 'roman_ur', 'policy_qa', $3::uuid)
                RETURNING id;
                """,
                globex.tenant_id,
                sess_b,
                tkt_b,
            )
        )
        created_ids["chat_messages"] = (msg_a, msg_b)

        fb_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.answer_feedback (tenant_id, message_id, user_id, rating, comment)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'up', 'Clear answer')
                RETURNING id;
                """,
                acme.tenant_id,
                msg_a,
                acme.employee.user_id,
            )
        )
        fb_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.answer_feedback (tenant_id, message_id, user_id, rating, comment)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'down', 'Missing citation')
                RETURNING id;
                """,
                globex.tenant_id,
                msg_b,
                globex.employee.user_id,
            )
        )
        created_ids["answer_feedback"] = (fb_a, fb_b)

        tm_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.ticket_messages (tenant_id, ticket_id, author_user_id, body, is_internal)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'Acme public reply', false)
                RETURNING id;
                """,
                acme.tenant_id,
                tkt_a,
                acme.owner.user_id,
            )
        )
        tm_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.ticket_messages (tenant_id, ticket_id, author_user_id, body, is_internal)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'Globex public reply', false)
                RETURNING id;
                """,
                globex.tenant_id,
                tkt_b,
                globex.owner.user_id,
            )
        )
        created_ids["ticket_messages"] = (tm_a, tm_b)

        rule_a = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.ticket_assignment_rules (
                    tenant_id, category, default_assignee_user_id, default_assignee_role,
                    sla_hours, reminder_before_hours, reopen_window_days
                )
                VALUES ($1::uuid, 'general', $2::uuid, 'hr_head', 24, 4, 7)
                ON CONFLICT (tenant_id, category) DO UPDATE SET updated_at = now()
                RETURNING id;
                """,
                acme.tenant_id,
                acme.owner.user_id,
            )
        )
        rule_b = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.ticket_assignment_rules (
                    tenant_id, category, default_assignee_user_id, default_assignee_role,
                    sla_hours, reminder_before_hours, reopen_window_days
                )
                VALUES ($1::uuid, 'general', $2::uuid, 'hr_head', 48, 6, 7)
                ON CONFLICT (tenant_id, category) DO UPDATE SET updated_at = now()
                RETURNING id;
                """,
                globex.tenant_id,
                globex.owner.user_id,
            )
        )
        created_ids["ticket_assignment_rules"] = (rule_a, rule_b)

        # 4. Run 6-point isolation check (including cross-tenant INSERT rejection)
        insert_factories = {
            "chat_sessions": lambda target_t: (
                (
                    "INSERT INTO public.chat_sessions (tenant_id, user_id, title, language) "
                    "VALUES ($1::uuid, $2::uuid, 'Cross Insert', 'en');"
                ),
                [target_t, globex.employee.user_id],
            ),
            "tickets": lambda target_t: (
                (
                    "INSERT INTO public.tickets (tenant_id, ticket_number, requester_user_id, "
                    "category, subject, description, sla_due_at, sla_reminder_at) "
                    "VALUES ($1::uuid, $2, $3::uuid, 'general', 'Cross', 'Cross', $4, $5);"
                ),
                [
                    target_t,
                    f"TKT-X-{uuid.uuid4().hex[:6]}",
                    globex.employee.user_id,
                    due_at,
                    reminder_at,
                ],
            ),
            "chat_messages": lambda target_t: (
                (
                    "INSERT INTO public.chat_messages (tenant_id, session_id, role, content, language) "
                    "VALUES ($1::uuid, $2::uuid, 'user', 'Cross', 'en');"
                ),
                [target_t, sess_b],
            ),
            "answer_feedback": lambda target_t: (
                (
                    "INSERT INTO public.answer_feedback (tenant_id, message_id, user_id, rating) "
                    "VALUES ($1::uuid, $2::uuid, $3::uuid, 'up');"
                ),
                [target_t, msg_b, globex.owner.user_id],
            ),
            "ticket_messages": lambda target_t: (
                (
                    "INSERT INTO public.ticket_messages (tenant_id, ticket_id, author_user_id, body) "
                    "VALUES ($1::uuid, $2::uuid, $3::uuid, 'Cross');"
                ),
                [target_t, tkt_b, globex.owner.user_id],
            ),
            "ticket_assignment_rules": lambda target_t: (
                (
                    "INSERT INTO public.ticket_assignment_rules (tenant_id, category) "
                    "VALUES ($1::uuid, 'facilities');"
                ),
                [target_t],
            ),
        }

        for tbl in STAGE7_TABLES:
            id_a, id_b = created_ids[tbl]
            await assert_table_tenant_isolation(
                table_name=tbl,
                company_a_tenant_id=acme.tenant_id,
                company_b_tenant_id=globex.tenant_id,
                company_a_row_id=id_a,
                company_b_row_id=id_b,
                insert_sql_factory=insert_factories[tbl],
            )

        # 5. Verify TRUNCATE is revoked on all 6 tables for agentic_hr_app
        app_conn = await get_app_connection()
        try:
            for tbl in STAGE7_TABLES:
                with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                    await app_conn.execute(f"TRUNCATE TABLE public.{tbl};")
        finally:
            await app_conn.close()
    finally:
        for tbl in (
            "ticket_assignment_rules",
            "ticket_messages",
            "answer_feedback",
            "chat_messages",
            "tickets",
            "chat_sessions",
        ):
            if tbl in created_ids:
                await admin_conn.execute(
                    f"DELETE FROM public.{tbl} WHERE id = ANY($1::uuid[]);",
                    list(created_ids[tbl]),
                )
        await admin_conn.close()


# ==============================================================================
# 2. Unique & CHECK Constraints (Including `roman_ur` & SLA Bounds)
# ==============================================================================
@pytest.mark.asyncio
async def test_02_stage7_unique_and_check_constraints(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies tenant-scoped unique constraints and CHECK constraints across Stage 7 tables,
    including `'roman_ur'` language support and `policy_documents.doc_scope` validation.
    """
    acme = isolation_context.company_a
    globex = isolation_context.company_b
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)

    created_sessions: list[str] = []
    created_tickets: list[str] = []
    created_messages: list[str] = []
    created_feedback: list[str] = []
    created_rules: list[str] = []
    created_docs: list[str] = []

    now = datetime.now(UTC)
    reminder_at = now + timedelta(hours=20)
    due_at = now + timedelta(hours=24)
    shared_ticket_number = f"TKT-2026-{uuid.uuid4().hex[:6]}"

    try:
        # 1. Duplicate (tenant_id, ticket_number) fails inside Acme, succeeds across Globex
        t_a1 = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.tickets (
                    tenant_id, ticket_number, requester_user_id, category,
                    subject, description, sla_due_at, sla_reminder_at
                )
                VALUES ($1::uuid, $2, $3::uuid, 'payroll', 'S1', 'D1', $4, $5)
                RETURNING id;
                """,
                acme.tenant_id,
                shared_ticket_number,
                acme.employee.user_id,
                due_at,
                reminder_at,
            )
        )
        created_tickets.append(t_a1)

        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await admin_conn.execute(
                """
                INSERT INTO public.tickets (
                    tenant_id, ticket_number, requester_user_id, category,
                    subject, description, sla_due_at, sla_reminder_at
                )
                VALUES ($1::uuid, $2, $3::uuid, 'payroll', 'S2', 'D2', $4, $5);
                """,
                acme.tenant_id,
                shared_ticket_number,
                acme.employee.user_id,
                due_at,
                reminder_at,
            )

        t_b1 = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.tickets (
                    tenant_id, ticket_number, requester_user_id, category,
                    subject, description, sla_due_at, sla_reminder_at
                )
                VALUES ($1::uuid, $2, $3::uuid, 'payroll', 'S3', 'D3', $4, $5)
                RETURNING id;
                """,
                globex.tenant_id,
                shared_ticket_number,
                globex.employee.user_id,
                due_at,
                reminder_at,
            )
        )
        created_tickets.append(t_b1)

        # 2. Ticket CHECK constraints (rating 1..5, reminder <= due, valid category)
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await admin_conn.execute(
                """
                INSERT INTO public.tickets (
                    tenant_id, ticket_number, requester_user_id, category,
                    subject, description, sla_due_at, sla_reminder_at, satisfaction_rating
                )
                VALUES ($1::uuid, $2, $3::uuid, 'general', 'Bad Rating', 'Desc', $4, $5, 6);
                """,
                acme.tenant_id,
                f"TKT-BAD-{uuid.uuid4().hex[:6]}",
                acme.employee.user_id,
                due_at,
                reminder_at,
            )

        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await admin_conn.execute(
                """
                INSERT INTO public.tickets (
                    tenant_id, ticket_number, requester_user_id, category,
                    subject, description, sla_due_at, sla_reminder_at
                )
                VALUES ($1::uuid, $2, $3::uuid, 'general', 'Bad SLA', 'Desc', $4, $5);
                """,
                acme.tenant_id,
                f"TKT-SLA-{uuid.uuid4().hex[:6]}",
                acme.employee.user_id,
                reminder_at,
                due_at,
            )

        # 3. Language CHECK ('en', 'ur', 'roman_ur', 'mixed') & unique answer_feedback
        sess_roman = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.chat_sessions (tenant_id, user_id, title, language)
                VALUES ($1::uuid, $2::uuid, 'Roman Urdu Session', 'roman_ur')
                RETURNING id;
                """,
                acme.tenant_id,
                acme.employee.user_id,
            )
        )
        created_sessions.append(sess_roman)

        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await admin_conn.execute(
                """
                INSERT INTO public.chat_sessions (tenant_id, user_id, title, language)
                VALUES ($1::uuid, $2::uuid, 'Unsupported Lang', 'fr');
                """,
                acme.tenant_id,
                acme.employee.user_id,
            )

        msg_roman = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.chat_messages (tenant_id, session_id, role, content, language)
                VALUES ($1::uuid, $2::uuid, 'assistant', 'Chutti ki policy...', 'roman_ur')
                RETURNING id;
                """,
                acme.tenant_id,
                sess_roman,
            )
        )
        created_messages.append(msg_roman)

        fb_id = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.answer_feedback (tenant_id, message_id, user_id, rating)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'up')
                RETURNING id;
                """,
                acme.tenant_id,
                msg_roman,
                acme.employee.user_id,
            )
        )
        created_feedback.append(fb_id)

        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await admin_conn.execute(
                """
                INSERT INTO public.answer_feedback (tenant_id, message_id, user_id, rating)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'down');
                """,
                acme.tenant_id,
                msg_roman,
                acme.employee.user_id,
            )

        # 4. Unique (tenant_id, category) & reminder < sla_hours on ticket_assignment_rules
        rule_id = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.ticket_assignment_rules (
                    tenant_id, category, default_assignee_role, sla_hours, reminder_before_hours
                )
                VALUES ($1::uuid, 'facilities', 'manager', 24, 4)
                RETURNING id;
                """,
                acme.tenant_id,
            )
        )
        created_rules.append(rule_id)

        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await admin_conn.execute(
                """
                INSERT INTO public.ticket_assignment_rules (
                    tenant_id, category, default_assignee_role, sla_hours, reminder_before_hours
                )
                VALUES ($1::uuid, 'facilities', 'hr_head', 12, 2);
                """,
                acme.tenant_id,
            )

        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await admin_conn.execute(
                """
                INSERT INTO public.ticket_assignment_rules (
                    tenant_id, category, default_assignee_role, sla_hours, reminder_before_hours
                )
                VALUES ($1::uuid, 'it_access', 'hr_head', 8, 8);
                """,
                acme.tenant_id,
            )

        # 5. policy_documents.doc_scope CHECK constraint
        doc_id = str(
            await admin_conn.fetchval(
                """
                INSERT INTO public.policy_documents (
                    tenant_id, title, doc_type, doc_scope, visibility,
                    file_name, mime_type, storage_key
                )
                VALUES (
                    $1::uuid, $2, 'helpdesk_knowledge', 'helpdesk_kb', 'all',
                    'vpn.pdf', 'application/pdf', $3
                )
                RETURNING id;
                """,
                acme.tenant_id,
                f"Helpdesk VPN Guide {uuid.uuid4().hex[:6]}",
                f"local/helpdesk/{uuid.uuid4().hex}.pdf",
            )
        )
        created_docs.append(doc_id)

        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await admin_conn.execute(
                """
                INSERT INTO public.policy_documents (
                    tenant_id, title, doc_type, doc_scope, visibility,
                    file_name, mime_type, storage_key
                )
                VALUES (
                    $1::uuid, $2, 'company_policy', 'invalid_scope', 'all',
                    'bad.pdf', 'application/pdf', $3
                );
                """,
                acme.tenant_id,
                f"Bad Scope {uuid.uuid4().hex[:6]}",
                f"local/bad/{uuid.uuid4().hex}.pdf",
            )
    finally:
        if created_docs:
            await admin_conn.execute(
                "DELETE FROM public.policy_documents WHERE id = ANY($1::uuid[]);",
                created_docs,
            )
        if created_rules:
            await admin_conn.execute(
                "DELETE FROM public.ticket_assignment_rules WHERE id = ANY($1::uuid[]);",
                created_rules,
            )
        if created_feedback:
            await admin_conn.execute(
                "DELETE FROM public.answer_feedback WHERE id = ANY($1::uuid[]);",
                created_feedback,
            )
        if created_messages:
            await admin_conn.execute(
                "DELETE FROM public.chat_messages WHERE id = ANY($1::uuid[]);",
                created_messages,
            )
        if created_tickets:
            await admin_conn.execute(
                "DELETE FROM public.tickets WHERE id = ANY($1::uuid[]);",
                created_tickets,
            )
        if created_sessions:
            await admin_conn.execute(
                "DELETE FROM public.chat_sessions WHERE id = ANY($1::uuid[]);",
                created_sessions,
            )
        await admin_conn.close()


# ==============================================================================
# 3. Secure View `pub_ticket_summary` Isolation & Internal Note Exclusion
# ==============================================================================
@pytest.mark.asyncio
async def test_03_pub_ticket_summary_view_isolation_and_internal_note_exclusion(
    isolation_context: MultiTenantTestContext,
) -> None:
    """
    Verifies `public.pub_ticket_summary` has `security_invoker=true`, `security_barrier=true`,
    is owned by `agentic_hr_migrator`, denies write operations to `agentic_hr_app`, isolates
    tenants, returns non-null `0` when zero public messages exist, and strictly excludes
    internal notes (`is_internal = true`) and sensitive descriptions.
    """
    acme = isolation_context.company_a
    globex = isolation_context.company_b
    admin_conn = await asyncpg.connect(MIGRATION_DB_URL)

    tkt_a_id = str(uuid.uuid4())
    tkt_a_empty_id = str(uuid.uuid4())
    tkt_b_id = str(uuid.uuid4())
    secret_note = "SECRET_HR_INTERNAL_NOTE_DO_NOT_LEAK"
    secret_description = "PRIVATE_DESCRIPTION_DO_NOT_EXPOSE_IN_VIEW"

    try:
        view_meta = await admin_conn.fetchrow(
            """
            SELECT c.relname, c.reloptions, pg_get_userbyid(c.relowner) AS owner
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'pub_ticket_summary';
            """
        )
        assert view_meta is not None, "public.pub_ticket_summary view missing"
        opts = set(view_meta["reloptions"] or [])
        assert "security_invoker=true" in opts
        assert "security_barrier=true" in opts
        assert view_meta["owner"] == "agentic_hr_migrator"

        # Verify excluded sensitive columns on view definition
        view_cols = await admin_conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'pub_ticket_summary';
            """
        )
        col_names = {r["column_name"] for r in view_cols}
        assert "public_message_count" in col_names
        assert "is_sla_breached" in col_names
        assert "description" not in col_names
        assert "body" not in col_names
        assert "is_internal" not in col_names

        now = datetime.now(UTC)
        reminder_at = now + timedelta(hours=20)
        due_at = now + timedelta(hours=24)

        await admin_conn.execute(
            """
            INSERT INTO public.tickets (
                id, tenant_id, ticket_number, requester_user_id, assigned_to_user_id,
                category, priority, status, subject, description, source,
                sla_due_at, sla_reminder_at
            )
            VALUES
              (
                $1::uuid, $2::uuid, $3, $4::uuid, $5::uuid,
                'hr_policy', 'high', 'in_progress', 'Acme View Ticket', $6, 'manual',
                $7, $8
              ),
              (
                $9::uuid, $2::uuid, $10, $4::uuid, NULL,
                'general', 'low', 'open', 'Acme Zero Msg Ticket', $6, 'manual',
                $7, $8
              ),
              (
                $11::uuid, $12::uuid, $13, $14::uuid, $15::uuid,
                'payroll', 'medium', 'open', 'Globex View Ticket', $6, 'manual',
                $7, $8
              );
            """,
            tkt_a_id,
            acme.tenant_id,
            f"TKT-VA1-{uuid.uuid4().hex[:6]}",
            acme.employee.user_id,
            acme.owner.user_id,
            secret_description,
            due_at,
            reminder_at,
            tkt_a_empty_id,
            f"TKT-VA0-{uuid.uuid4().hex[:6]}",
            tkt_b_id,
            globex.tenant_id,
            f"TKT-VB1-{uuid.uuid4().hex[:6]}",
            globex.employee.user_id,
            globex.owner.user_id,
        )

        await admin_conn.execute(
            """
            INSERT INTO public.ticket_messages (tenant_id, ticket_id, author_user_id, body, is_internal)
            VALUES
              ($1::uuid, $2::uuid, $3::uuid, 'Public clarification reply', false),
              ($1::uuid, $2::uuid, $3::uuid, $4, true);
            """,
            acme.tenant_id,
            tkt_a_id,
            acme.owner.user_id,
            secret_note,
        )

        app_conn = await get_app_connection()
        try:
            # 1. Uncontexted -> 0 rows
            assert await app_conn.fetchval("SELECT count(*) FROM public.pub_ticket_summary;") == 0

            # 2. Bogus tenant -> 0 rows
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', true);"
                )
                assert (
                    await app_conn.fetchval("SELECT count(*) FROM public.pub_ticket_summary;") == 0
                )

            # 3. Acme context -> sees only Acme rows, non-null public_message_count, no secret text
            async with app_conn.transaction():
                await app_conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true);",
                    acme.tenant_id,
                )
                rows = await app_conn.fetch(
                    """
                    SELECT * FROM public.pub_ticket_summary
                    WHERE ticket_id = ANY($1::uuid[]);
                    """,
                    [tkt_a_id, tkt_a_empty_id, tkt_b_id],
                )
                by_id = {str(r["ticket_id"]): dict(r) for r in rows}
                assert set(by_id.keys()) == {tkt_a_id, tkt_a_empty_id}
                assert by_id[tkt_a_id]["public_message_count"] == 1
                assert by_id[tkt_a_empty_id]["public_message_count"] == 0
                assert by_id[tkt_a_id]["is_sla_breached"] is False
                serialized = repr(by_id)
                assert secret_note not in serialized
                assert secret_description not in serialized

            # 4. Write operation on view denied to agentic_hr_app
            privs = await app_conn.fetchrow(
                """
                SELECT
                    has_table_privilege('agentic_hr_app', 'public.pub_ticket_summary', 'SELECT') AS can_select,
                    has_table_privilege('agentic_hr_app', 'public.pub_ticket_summary', 'INSERT') AS can_insert,
                    has_table_privilege('agentic_hr_app', 'public.pub_ticket_summary', 'UPDATE') AS can_update,
                    has_table_privilege('agentic_hr_app', 'public.pub_ticket_summary', 'DELETE') AS can_delete;
                """
            )
            assert privs["can_select"] is True
            assert privs["can_insert"] is False
            assert privs["can_update"] is False
            assert privs["can_delete"] is False

            with pytest.raises(
                (
                    asyncpg.exceptions.InsufficientPrivilegeError,
                    asyncpg.exceptions.ObjectNotInPrerequisiteStateError,
                )
            ):
                await app_conn.execute(
                    "DELETE FROM public.pub_ticket_summary WHERE ticket_id = $1::uuid;",
                    tkt_a_id,
                )
        finally:
            await app_conn.close()
    finally:
        await admin_conn.execute(
            "DELETE FROM public.ticket_messages WHERE ticket_id = ANY($1::uuid[]);",
            [tkt_a_id, tkt_a_empty_id, tkt_b_id],
        )
        await admin_conn.execute(
            "DELETE FROM public.tickets WHERE id = ANY($1::uuid[]);",
            [tkt_a_id, tkt_a_empty_id, tkt_b_id],
        )
        await admin_conn.close()


# ==============================================================================
# 4. Pydantic Contract Validation for Stage 7 Chat, Citations & Tickets
# ==============================================================================
def test_04_stage7_pydantic_schemas_validation() -> None:
    """
    Validates CitationItem optional/flexible `page` normalization, `roman_ur` support
    in `PolicyBrainAnswer`, `TicketCloseRequest` bounds, and `TicketAssignmentRuleUpsertRequest`.
    """
    chunk_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    c_none = CitationItem(
        chunk_id=chunk_id,
        document_id=doc_id,
        title="Leave Policy",
        version=2,
        section="Annual Leave",
        page=None,
        snippet="Employees receive 20 annual leave days.",
    )
    assert c_none.page is None

    c_int = CitationItem(
        chunk_id=chunk_id,
        document_id=doc_id,
        title="Leave Policy",
        version=2,
        section="Annual Leave",
        page="4",
        snippet="Employees receive 20 annual leave days.",
    )
    assert c_int.page == 4

    c_range = CitationItem(
        chunk_id=chunk_id,
        document_id=doc_id,
        title="Leave Policy",
        version=2,
        section="Annual Leave",
        page="4-5",
        snippet="Employees receive 20 annual leave days.",
    )
    assert c_range.page == "4-5"

    ans = PolicyBrainAnswer(
        answer="Aap ko saalana 20 din ki chutti milti hai.",
        language="roman_ur",
        intent="policy_qa",
        citations=[c_int],
        abstained=False,
    )
    assert ans.language == "roman_ur"
    assert len(ans.citations) == 1

    close_ok = TicketCloseRequest(satisfaction_rating=5, satisfaction_comment="Fast help")
    assert close_ok.satisfaction_rating == 5
    with pytest.raises(ValidationError):
        TicketCloseRequest(satisfaction_rating=0)

    rule_ok = TicketAssignmentRuleUpsertRequest(
        category="hr_policy",
        sla_hours=24,
        reminder_before_hours=4,
        reopen_window_days=7,
    )
    assert rule_ok.sla_hours == 24
    with pytest.raises(ValidationError):
        TicketAssignmentRuleUpsertRequest(
            category="hr_policy",
            sla_hours=12,
            reminder_before_hours=12,
        )
