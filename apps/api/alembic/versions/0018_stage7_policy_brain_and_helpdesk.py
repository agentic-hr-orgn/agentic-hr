"""stage 7 prompt 1: policy brain & helpdesk schema, forced RLS & pub_ticket_summary view

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-23 05:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.rls import (
    create_tenant_rls_policies,
    disable_rls,
    drop_tenant_rls_policies,
    enable_rls,
)

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGE7_TABLES: tuple[str, ...] = (
    "chat_sessions",
    "tickets",
    "chat_messages",
    "answer_feedback",
    "ticket_messages",
    "ticket_assignment_rules",
)


def upgrade() -> None:
    # ---------------------------------------------------------
    # 1. Extend public.policy_documents with doc_scope
    #    (Preserve Stage 5 `visibility` IN ('all', 'managers', 'hr_only'))
    # ---------------------------------------------------------
    op.add_column(
        "policy_documents",
        sa.Column(
            "doc_scope",
            sa.String(length=24),
            server_default="hr_policy",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_policy_documents_doc_scope",
        "policy_documents",
        "doc_scope IN ('hr_policy', 'helpdesk_kb')",
    )
    op.execute(
        "UPDATE public.policy_documents "
        "SET doc_scope = 'helpdesk_kb' "
        "WHERE doc_type = 'helpdesk_knowledge';"
    )
    op.create_index(
        "ix_policy_documents_tenant_doc_scope",
        "policy_documents",
        ["tenant_id", "doc_scope", "visibility"],
    )

    # ---------------------------------------------------------
    # 2. Create public.chat_sessions
    # ---------------------------------------------------------
    op.create_table(
        "chat_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "title",
            sa.String(length=255),
            server_default="New conversation",
            nullable=False,
        ),
        sa.Column(
            "language",
            sa.String(length=16),
            server_default="en",
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "language IN ('en', 'ur', 'roman_ur', 'mixed')",
            name="ck_chat_sessions_language",
        ),
    )
    op.create_index("ix_chat_sessions_tenant_id", "chat_sessions", ["tenant_id"])
    op.create_index(
        "ix_chat_sessions_tenant_user_last_msg",
        "chat_sessions",
        ["tenant_id", "user_id", "last_message_at"],
    )

    # ---------------------------------------------------------
    # 3. Create public.tickets (before chat_messages FK to tickets.id)
    # ---------------------------------------------------------
    op.create_table(
        "tickets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ticket_number", sa.Text(), nullable=False),
        sa.Column(
            "requester_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_to_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column(
            "priority",
            sa.String(length=16),
            server_default="medium",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="open",
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            server_default="manual",
            nullable=False,
        ),
        sa.Column(
            "source_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sla_reminder_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sla_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopen_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("satisfaction_rating", sa.SmallInteger(), nullable=True),
        sa.Column("satisfaction_comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "ticket_number",
            name="uq_tickets_tenant_ticket_number",
        ),
        sa.CheckConstraint(
            "category IN ('hr_policy', 'leave_attendance', 'payroll', 'it_access', 'facilities', 'general')",
            name="ck_tickets_category",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'urgent')",
            name="ck_tickets_priority",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'waiting_on_employee', 'resolved', 'closed')",
            name="ck_tickets_status",
        ),
        sa.CheckConstraint(
            "source IN ('chat_abstain', 'chat_ask_hr', 'helpdesk_agent', 'manual')",
            name="ck_tickets_source",
        ),
        sa.CheckConstraint(
            "sla_reminder_at <= sla_due_at",
            name="ck_tickets_sla_order",
        ),
        sa.CheckConstraint(
            "satisfaction_rating IS NULL OR (satisfaction_rating >= 1 AND satisfaction_rating <= 5)",
            name="ck_tickets_satisfaction_rating",
        ),
    )
    op.create_index("ix_tickets_tenant_id", "tickets", ["tenant_id"])
    op.create_index("ix_tickets_tenant_status", "tickets", ["tenant_id", "status"])
    op.create_index("ix_tickets_tenant_requester", "tickets", ["tenant_id", "requester_user_id"])
    op.create_index("ix_tickets_tenant_assignee", "tickets", ["tenant_id", "assigned_to_user_id"])
    op.create_index("ix_tickets_tenant_sla_due", "tickets", ["tenant_id", "sla_due_at"])

    # ---------------------------------------------------------
    # 4. Create public.chat_messages
    # ---------------------------------------------------------
    op.create_table(
        "chat_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "language",
            sa.String(length=16),
            server_default="en",
            nullable=False,
        ),
        sa.Column("intent", sa.String(length=64), nullable=True),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "abstained",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "ticket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tickets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("handoff_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("topic_tag", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("model_tier", sa.Text(), nullable=True),
        sa.Column("token_usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_chat_messages_role",
        ),
        sa.CheckConstraint(
            "language IN ('en', 'ur', 'roman_ur', 'mixed')",
            name="ck_chat_messages_language",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(citations) = 'array'",
            name="ck_chat_messages_citations_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(tool_calls) = 'array'",
            name="ck_chat_messages_tool_calls_array",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_chat_messages_latency_non_neg",
        ),
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="ck_chat_messages_cost_non_neg",
        ),
    )
    op.create_index("ix_chat_messages_tenant_id", "chat_messages", ["tenant_id"])
    op.create_index(
        "ix_chat_messages_tenant_session_created",
        "chat_messages",
        ["tenant_id", "session_id", "created_at"],
    )
    op.create_index("ix_chat_messages_ticket_id", "chat_messages", ["ticket_id"])

    # ---------------------------------------------------------
    # 5. Create public.answer_feedback
    # ---------------------------------------------------------
    op.create_table(
        "answer_feedback",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("topic_tag", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "message_id",
            "user_id",
            name="uq_answer_feedback_tenant_msg_user",
        ),
        sa.CheckConstraint(
            "rating IN ('up', 'down')",
            name="ck_answer_feedback_rating",
        ),
    )
    op.create_index("ix_answer_feedback_tenant_id", "answer_feedback", ["tenant_id"])
    op.create_index(
        "ix_answer_feedback_tenant_message",
        "answer_feedback",
        ["tenant_id", "message_id"],
    )

    # ---------------------------------------------------------
    # 6. Create public.ticket_messages
    # ---------------------------------------------------------
    op.create_table(
        "ticket_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ticket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "is_internal",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ticket_messages_tenant_id", "ticket_messages", ["tenant_id"])
    op.create_index(
        "ix_ticket_messages_tenant_ticket_created",
        "ticket_messages",
        ["tenant_id", "ticket_id", "created_at"],
    )

    # ---------------------------------------------------------
    # 7. Create public.ticket_assignment_rules
    # ---------------------------------------------------------
    op.create_table(
        "ticket_assignment_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column(
            "default_assignee_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "default_assignee_role",
            sa.String(length=24),
            server_default="hr_head",
            nullable=False,
        ),
        sa.Column(
            "sla_hours",
            sa.Integer(),
            server_default=sa.text("24"),
            nullable=False,
        ),
        sa.Column(
            "reminder_before_hours",
            sa.Integer(),
            server_default=sa.text("4"),
            nullable=False,
        ),
        sa.Column(
            "reopen_window_days",
            sa.Integer(),
            server_default=sa.text("7"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "category",
            name="uq_ticket_assignment_rules_tenant_category",
        ),
        sa.CheckConstraint(
            "category IN ('hr_policy', 'leave_attendance', 'payroll', 'it_access', 'facilities', 'general')",
            name="ck_ticket_assignment_rules_category",
        ),
        sa.CheckConstraint(
            "default_assignee_role IN ('hr_head', 'owner', 'manager')",
            name="ck_ticket_assignment_rules_assignee_role",
        ),
        sa.CheckConstraint(
            "sla_hours >= 1",
            name="ck_ticket_assignment_rules_sla_hours_pos",
        ),
        sa.CheckConstraint(
            "reminder_before_hours >= 0 AND reminder_before_hours < sla_hours",
            name="ck_ticket_assignment_rules_reminder_hours",
        ),
        sa.CheckConstraint(
            "reopen_window_days >= 0",
            name="ck_ticket_assignment_rules_reopen_days",
        ),
    )
    op.create_index(
        "ix_ticket_assignment_rules_tenant_id",
        "ticket_assignment_rules",
        ["tenant_id"],
    )

    # ---------------------------------------------------------
    # 8. Enforce Forced RLS, Ownership & Grants on all 6 tables
    # ---------------------------------------------------------
    for tbl in STAGE7_TABLES:
        qualified = f"public.{tbl}"
        enable_rls(qualified)
        create_tenant_rls_policies(qualified)
        op.execute(f"ALTER TABLE {qualified} OWNER TO agentic_hr_migrator;")
        op.execute(f"GRANT ALL ON TABLE {qualified} TO agentic_hr_migrator;")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {qualified} TO agentic_hr_app;")
        op.execute(f"REVOKE TRUNCATE ON TABLE {qualified} FROM agentic_hr_app;")

    # ---------------------------------------------------------
    # 9. Create Secure Read View public.pub_ticket_summary
    #    (Excludes internal notes, ticket descriptions, and user PII)
    # ---------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW public.pub_ticket_summary
        WITH (security_invoker = true, security_barrier = true) AS
        SELECT
            t.id AS ticket_id,
            t.tenant_id,
            t.ticket_number,
            t.requester_user_id,
            req.name AS requester_name,
            t.assigned_to_user_id,
            asg.name AS assignee_name,
            t.category,
            t.priority,
            t.status,
            t.subject,
            t.source,
            t.sla_due_at,
            t.sla_reminder_at,
            t.sla_escalated_at,
            (
                t.sla_escalated_at IS NOT NULL
                OR (t.status NOT IN ('resolved', 'closed') AND t.sla_due_at < now())
            ) AS is_sla_breached,
            COALESCE(pm.public_message_count, 0)::int AS public_message_count,
            t.resolved_at,
            t.closed_at,
            t.satisfaction_rating,
            t.created_at,
            t.updated_at
        FROM public.tickets t
        LEFT JOIN public.users req
          ON t.requester_user_id = req.id
        LEFT JOIN public.users asg
          ON t.assigned_to_user_id = asg.id
        LEFT JOIN (
            SELECT
                tm.tenant_id,
                tm.ticket_id,
                COUNT(*)::int AS public_message_count
            FROM public.ticket_messages tm
            WHERE tm.is_internal = false
            GROUP BY tm.tenant_id, tm.ticket_id
        ) pm
          ON pm.ticket_id = t.id AND pm.tenant_id = t.tenant_id;
        """
    )
    op.execute("ALTER VIEW public.pub_ticket_summary OWNER TO agentic_hr_migrator;")
    op.execute("REVOKE ALL ON public.pub_ticket_summary FROM PUBLIC;")
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.pub_ticket_summary FROM agentic_hr_app;"
    )
    op.execute("GRANT SELECT ON public.pub_ticket_summary TO agentic_hr_app;")


def downgrade() -> None:
    # 1. Drop secure view
    op.execute("DROP VIEW IF EXISTS public.pub_ticket_summary CASCADE;")

    # 2. Drop RLS policies, disable RLS, and drop Stage 7 tables in reverse dependency order
    for tbl in reversed(STAGE7_TABLES):
        qualified = f"public.{tbl}"
        drop_tenant_rls_policies(qualified)
        disable_rls(qualified)
        op.drop_table(tbl)

    # 3. Revert policy_documents doc_scope addition
    op.drop_index("ix_policy_documents_tenant_doc_scope", table_name="policy_documents")
    op.drop_constraint("ck_policy_documents_doc_scope", "policy_documents", type_="check")
    op.drop_column("policy_documents", "doc_scope")
