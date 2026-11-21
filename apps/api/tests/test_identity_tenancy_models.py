"""
Tests for Stage 2, Phase 1: Identity and Tenancy Database Foundation.
Verifies models, constraints, multi-tenant user memberships, and cascade rules.
"""

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.membership import Membership
from app.models.role import Role
from app.models.tenant_entity import Tenant
from app.models.user import User

# Schema and model definition tests execute as the migration / schema owner
schema_engine = create_async_engine(
    settings.MIGRATION_DATABASE_URL,
    echo=False,
    future=True,
    poolclass=NullPool,
)
SchemaSessionLocal = async_sessionmaker(
    bind=schema_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)
AsyncSessionLocal = SchemaSessionLocal


@pytest.mark.asyncio
async def test_roles_seeded_and_unique() -> None:
    """Verifies that system roles ('owner', 'manager', 'employee') exist and are unique."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Role))
        roles = result.scalars().all()
        role_keys = {r.role_key for r in roles}

        assert "owner" in role_keys
        assert "manager" in role_keys
        assert "employee" in role_keys

        # Verify duplicate role_key is rejected
        with pytest.raises(IntegrityError):
            duplicate_role = Role(
                role_key="owner",
                name="Duplicate Owner",
                description="Should fail",
            )
            session.add(duplicate_role)
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_create_tenant_and_uniqueness() -> None:
    """Verifies tenant creation and clerk_org_id uniqueness constraint."""
    org_id = f"org_test_{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        tenant = Tenant(
            name="Test Corp",
            clerk_org_id=org_id,
            status="active",
        )
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)

        assert tenant.id is not None
        assert tenant.name == "Test Corp"
        assert tenant.clerk_org_id == org_id
        assert tenant.created_at is not None

        # Verify duplicate clerk_org_id is rejected
        with pytest.raises(IntegrityError):
            duplicate_tenant = Tenant(
                name="Another Corp",
                clerk_org_id=org_id,
                status="active",
            )
            session.add(duplicate_tenant)
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_create_user_and_uniqueness() -> None:
    """Verifies user creation and clerk_user_id / email uniqueness constraints."""
    clerk_id = f"user_{uuid.uuid4().hex[:8]}"
    email = f"user_{uuid.uuid4().hex[:8]}@example.com"

    async with AsyncSessionLocal() as session:
        user = User(
            clerk_user_id=clerk_id,
            email=email,
            name="Alice Test",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        assert user.id is not None
        assert user.clerk_user_id == clerk_id
        assert user.email == email

        # Duplicate clerk_user_id
        with pytest.raises(IntegrityError):
            dup_clerk = User(
                clerk_user_id=clerk_id,
                email=f"other_{uuid.uuid4().hex[:8]}@example.com",
                name="Duplicate Clerk",
            )
            session.add(dup_clerk)
            await session.commit()
        await session.rollback()

        # Duplicate email
        with pytest.raises(IntegrityError):
            dup_email = User(
                clerk_user_id=f"user_{uuid.uuid4().hex[:8]}",
                email=email,
                name="Duplicate Email",
            )
            session.add(dup_email)
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_membership_creation_and_relationships() -> None:
    """Verifies membership binds user, tenant, and role with permission flags and ORM traversal."""
    async with AsyncSessionLocal() as session:
        # Retrieve owner role
        role_res = await session.execute(select(Role).where(Role.role_key == "owner"))
        owner_role = role_res.scalar_one()

        tenant = Tenant(
            name="Relationship Corp",
            clerk_org_id=f"org_rel_{uuid.uuid4().hex[:8]}",
        )
        user = User(
            clerk_user_id=f"user_rel_{uuid.uuid4().hex[:8]}",
            email=f"rel_{uuid.uuid4().hex[:8]}@example.com",
            name="Bob Relationship",
        )
        session.add_all([tenant, user])
        await session.commit()

        membership = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role_id=owner_role.id,
            is_hr_head=True,
            is_payroll_admin=True,
            status="active",
        )
        session.add(membership)
        await session.commit()
        await session.refresh(membership)

        assert membership.id is not None
        assert membership.is_hr_head is True
        assert membership.is_payroll_admin is True

        # Query back through relationships
        stmt = select(Membership).where(Membership.id == membership.id)
        res = await session.execute(stmt)
        loaded_mem = res.scalar_one()

        assert loaded_mem.tenant_id == tenant.id
        assert loaded_mem.user_id == user.id
        assert loaded_mem.role_id == owner_role.id


@pytest.mark.asyncio
async def test_user_multiple_tenancies() -> None:
    """
    Architecture Decision Verification:
    A single User can belong to multiple Tenants with different roles and permission flags.
    """
    async with AsyncSessionLocal() as session:
        roles_res = await session.execute(select(Role))
        roles = {r.role_key: r for r in roles_res.scalars().all()}

        # 1 user, 2 tenants
        user = User(
            clerk_user_id=f"user_multi_{uuid.uuid4().hex[:8]}",
            email=f"multi_{uuid.uuid4().hex[:8]}@example.com",
            name="Multi-Tenant User",
        )
        tenant_a = Tenant(
            name="Company Alpha",
            clerk_org_id=f"org_alpha_{uuid.uuid4().hex[:8]}",
        )
        tenant_b = Tenant(
            name="Company Beta",
            clerk_org_id=f"org_beta_{uuid.uuid4().hex[:8]}",
        )
        session.add_all([user, tenant_a, tenant_b])
        await session.commit()

        # Membership in Tenant A as Manager (HR Head)
        mem_a = Membership(
            tenant_id=tenant_a.id,
            user_id=user.id,
            role_id=roles["manager"].id,
            is_hr_head=True,
            is_payroll_admin=False,
        )
        # Membership in Tenant B as Employee (Standard)
        mem_b = Membership(
            tenant_id=tenant_b.id,
            user_id=user.id,
            role_id=roles["employee"].id,
            is_hr_head=False,
            is_payroll_admin=False,
        )
        session.add_all([mem_a, mem_b])
        await session.commit()

        # Query all memberships for this user
        res = await session.execute(select(Membership).where(Membership.user_id == user.id))
        user_memberships = res.scalars().all()
        assert len(user_memberships) == 2

        tenants_found = {m.tenant_id for m in user_memberships}
        assert tenant_a.id in tenants_found
        assert tenant_b.id in tenants_found


@pytest.mark.asyncio
async def test_duplicate_membership_prevention() -> None:
    """Verifies that a user cannot hold duplicate memberships in the SAME tenant."""
    async with AsyncSessionLocal() as session:
        role_res = await session.execute(select(Role).where(Role.role_key == "employee"))
        emp_role = role_res.scalar_one()

        tenant = Tenant(
            name="Duplicate Check Corp",
            clerk_org_id=f"org_dup_{uuid.uuid4().hex[:8]}",
        )
        user = User(
            clerk_user_id=f"user_dup_{uuid.uuid4().hex[:8]}",
            email=f"dup_{uuid.uuid4().hex[:8]}@example.com",
            name="Duplicate Member",
        )
        session.add_all([tenant, user])
        await session.commit()

        # First membership succeeds
        mem1 = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role_id=emp_role.id,
        )
        session.add(mem1)
        await session.commit()

        # Second membership for the same (tenant, user) pair must fail
        with pytest.raises(IntegrityError):
            mem2 = Membership(
                tenant_id=tenant.id,
                user_id=user.id,
                role_id=emp_role.id,
            )
            session.add(mem2)
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_cascade_delete_behavior() -> None:
    """Verifies deleting a Tenant cascades to its Memberships, but preserves the global User."""
    async with AsyncSessionLocal() as session:
        role_res = await session.execute(select(Role).where(Role.role_key == "employee"))
        emp_role = role_res.scalar_one()

        tenant = Tenant(
            name="Cascade Corp",
            clerk_org_id=f"org_casc_{uuid.uuid4().hex[:8]}",
        )
        user = User(
            clerk_user_id=f"user_casc_{uuid.uuid4().hex[:8]}",
            email=f"casc_{uuid.uuid4().hex[:8]}@example.com",
            name="Cascade Target",
        )
        session.add_all([tenant, user])
        await session.commit()

        mem = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role_id=emp_role.id,
        )
        session.add(mem)
        await session.commit()
        mem_id = mem.id
        user_id = user.id

        # Delete the Tenant
        await session.delete(tenant)
        await session.commit()

        # Membership should be gone (cascaded)
        mem_check = await session.execute(select(Membership).where(Membership.id == mem_id))
        assert mem_check.scalar_one_or_none() is None

        # Global user must still exist
        user_check = await session.execute(select(User).where(User.id == user_id))
        assert user_check.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_role_deletion_protection() -> None:
    """Verifies deleting an in-use Role is restricted by foreign key constraint."""
    async with AsyncSessionLocal() as session:
        # Create a custom role to test deletion restriction
        custom_role = Role(
            role_key=f"custom_{uuid.uuid4().hex[:6]}",
            name="Custom Role",
            description="Testing deletion restriction",
        )
        tenant = Tenant(
            name="Role Protect Corp",
            clerk_org_id=f"org_rp_{uuid.uuid4().hex[:8]}",
        )
        user = User(
            clerk_user_id=f"user_rp_{uuid.uuid4().hex[:8]}",
            email=f"rp_{uuid.uuid4().hex[:8]}@example.com",
            name="Role Protect User",
        )
        session.add_all([custom_role, tenant, user])
        await session.commit()

        mem = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role_id=custom_role.id,
        )
        session.add(mem)
        await session.commit()

        role_id = custom_role.id
        tenant_id = tenant.id
        user_id = user.id
        mem_id = mem.id

        # Attempt to delete custom_role -> should fail due to ondelete="RESTRICT"
        try:
            with pytest.raises(IntegrityError):
                await session.delete(custom_role)
                await session.commit()
            await session.rollback()
        finally:
            await session.rollback()
            await session.execute(delete(Membership).where(Membership.id == mem_id))
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.execute(delete(Role).where(Role.id == role_id))
            await session.commit()
