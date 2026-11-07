"""
Reusable Row-Level Security (RLS) Migration Helpers for Agentic HR Core.
Enforces strict fail-closed tenant isolation across tenant-scoped tables.
"""

from alembic import op


def enable_rls(table_name: str) -> None:
    """Enables and forces Row-Level Security on a table."""
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;")


def disable_rls(table_name: str) -> None:
    """Disables Row-Level Security on a table."""
    op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;")


def drop_tenant_rls_policies(table_name: str) -> None:
    """Drops all tenant RLS policies for a table."""
    clean_name = table_name.split(".")[-1]
    for op_type in ["select", "insert", "update", "delete"]:
        op.execute(f"DROP POLICY IF EXISTS {clean_name}_tenant_{op_type}_policy ON {table_name};")


def create_tenant_rls_policies(table_name: str, tenant_col: str = "tenant_id") -> None:
    """
    Creates fail-closed SELECT, INSERT, UPDATE, DELETE RLS policies.
    Guarantees that missing or empty setting denies all rows.
    """
    clean_name = table_name.split(".")[-1]
    condition = f"{tenant_col} = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

    # Drop existing policies to prevent duplicate / conflicting policies
    drop_tenant_rls_policies(table_name)

    # 1. SELECT Policy (USING)
    op.execute(f"""
        CREATE POLICY {clean_name}_tenant_select_policy ON {table_name}
        FOR SELECT
        USING ({condition});
    """)

    # 2. INSERT Policy (WITH CHECK)
    op.execute(f"""
        CREATE POLICY {clean_name}_tenant_insert_policy ON {table_name}
        FOR INSERT
        WITH CHECK ({condition});
    """)

    # 3. UPDATE Policy (USING and WITH CHECK)
    op.execute(f"""
        CREATE POLICY {clean_name}_tenant_update_policy ON {table_name}
        FOR UPDATE
        USING ({condition})
        WITH CHECK ({condition});
    """)

    # 4. DELETE Policy (USING)
    op.execute(f"""
        CREATE POLICY {clean_name}_tenant_delete_policy ON {table_name}
        FOR DELETE
        USING ({condition});
    """)
