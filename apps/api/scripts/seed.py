"""
Safe and Idempotent Synthetic Data Seeder for Agentic HR Core.
Complies with ADR 003, ADR 006, and docs/synthetic-data-plan.md.
Seeds PostgreSQL database with two companies (Acme Tech Solutions & Globex Corp)
and authorized test user accounts across multi-tenant memberships.
"""

import asyncio
import logging

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.membership import Membership
from app.models.role import Role
from app.models.tenant_entity import Tenant
from app.models.user import User
from app.services.synthetic_service import SyntheticDataService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [seed] %(message)s",
)
logger = logging.getLogger("agentic_hr.seed")


async def seed_database() -> None:
    """Idempotently seeds PostgreSQL database with roles, companies, users, and memberships."""
    logger.info("Initializing PostgreSQL database synthetic seeder...")

    async with AsyncSessionLocal() as session:
        # 1. Verify / Seed Roles
        logger.info("Verifying system roles in PostgreSQL...")
        role_definitions = [
            ("owner", "Owner", "Full administrative control over tenant settings, billing, and memberships"),
            ("manager", "Manager", "Operational manager with candidate review, interview scheduling, and team oversight"),
            ("employee", "Employee", "Standard staff access for leave requests, policy RAG queries, and personal directory"),
        ]
        for key, name, desc in role_definitions:
            stmt = select(Role).where(Role.role_key == key)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing:
                session.add(Role(role_key=key, name=name, description=desc))
        await session.commit()

        # Load roles lookup map
        roles_res = await session.execute(select(Role))
        roles = {r.role_key: r for r in roles_res.scalars().all()}
        logger.info(f"Loaded {len(roles)} system roles: {', '.join(roles.keys())}")

        # 2. Seed Two Companies (Tenants)
        companies_data = [
            ("Acme Tech Solutions", "org_acme_tech_01"),
            ("Globex Corporation", "org_globex_corp_02"),
        ]
        tenants: dict[str, Tenant] = {}
        for name, clerk_org_id in companies_data:
            stmt = select(Tenant).where(Tenant.clerk_org_id == clerk_org_id)
            tenant = (await session.execute(stmt)).scalar_one_or_none()
            if not tenant:
                tenant = Tenant(name=name, clerk_org_id=clerk_org_id, status="active")
                session.add(tenant)
                await session.flush()
                logger.info(f"[Tenant Created] {name} ({clerk_org_id}) -> {tenant.id}")
            else:
                logger.info(f"[Tenant Exists] {name} ({clerk_org_id}) -> {tenant.id}")
            tenants[clerk_org_id] = tenant
        await session.commit()

        # 3. Seed Users (ADR 006 Authorized Six Test Accounts)
        users_data = [
            ("agentic.hr.test.ubaid.owner@gmail.com", "user_test_owner_01", "Ubaid Owner"),
            ("agentic.hr.test.ubaid.recruiter@gmail.com", "user_test_recruiter_02", "Ubaid Recruiter"),
            ("agentic.hr.test.ubaid.hm@gmail.com", "user_test_hm_03", "Ubaid Hiring Manager"),
            ("agentic.hr.test.ubaid.employee@gmail.com", "user_test_employee_04", "Ubaid Staff Employee"),
            ("agentic.hr.test.ubaid.candidate1@gmail.com", "user_test_candidate1_05", "Ubaid Candidate One"),
            ("agentic.hr.test.ubaid.candidate2@gmail.com", "user_test_candidate2_06", "Ubaid Cross-Company Candidate Two"),
        ]
        users: dict[str, User] = {}
        for email, clerk_user_id, full_name in users_data:
            stmt = select(User).where(User.email == email)
            user = (await session.execute(stmt)).scalar_one_or_none()
            if not user:
                user = User(email=email, clerk_user_id=clerk_user_id, name=full_name)
                session.add(user)
                await session.flush()
                logger.info(f"[User Created] {full_name} ({email}) -> {user.id}")
            else:
                logger.info(f"[User Exists] {full_name} ({email}) -> {user.id}")
            users[email] = user
        await session.commit()

        # 4. Seed Memberships with Roles and Permission Flags
        # (user_email, clerk_org_id, role_key, is_hr_head, is_payroll_admin)
        memberships_data = [
            ("agentic.hr.test.ubaid.owner@gmail.com", "org_acme_tech_01", "owner", True, True),
            ("agentic.hr.test.ubaid.recruiter@gmail.com", "org_acme_tech_01", "manager", True, False),
            ("agentic.hr.test.ubaid.hm@gmail.com", "org_acme_tech_01", "manager", False, False),
            ("agentic.hr.test.ubaid.employee@gmail.com", "org_acme_tech_01", "employee", False, False),
            # Candidate 1 belongs to Globex Corporation
            ("agentic.hr.test.ubaid.candidate1@gmail.com", "org_globex_corp_02", "employee", False, False),
            # Candidate 2 belongs to BOTH Acme Tech Solutions AND Globex Corporation (Multi-Tenancy Proof)
            ("agentic.hr.test.ubaid.candidate2@gmail.com", "org_acme_tech_01", "employee", False, False),
            ("agentic.hr.test.ubaid.candidate2@gmail.com", "org_globex_corp_02", "manager", True, False),
        ]

        for email, org_id, role_key, hr_head, payroll_admin in memberships_data:
            target_user = users[email]
            target_tenant = tenants[org_id]
            target_role = roles[role_key]

            stmt = select(Membership).where(
                Membership.tenant_id == target_tenant.id,
                Membership.user_id == target_user.id,
            )
            membership = (await session.execute(stmt)).scalar_one_or_none()
            if not membership:
                membership = Membership(
                    tenant_id=target_tenant.id,
                    user_id=target_user.id,
                    role_id=target_role.id,
                    is_hr_head=hr_head,
                    is_payroll_admin=payroll_admin,
                    status="active",
                )
                session.add(membership)
                await session.flush()
                logger.info(
                    f"[Membership Created] {email} in {target_tenant.name} as {target_role.name} "
                    f"(HR Head: {hr_head}, Payroll: {payroll_admin})"
                )
            else:
                logger.info(f"[Membership Exists] {email} in {target_tenant.name}")

        await session.commit()
        logger.info("Database synthetic tenancy seeding completed successfully.")


def print_synthetic_metadata_summary() -> None:
    """Preserves Stage 1 in-memory synthetic service metadata verification."""
    company = SyntheticDataService.get_company_profile()
    employees = SyntheticDataService.get_synthetic_employees()
    policies = company.policy_documents

    logger.info("--------------------------------------------------")
    logger.info("SYNTHETIC DATA VERIFICATION SUMMARY (STAGE 2)")
    logger.info(f"- Company: {company.name} ({company.domain})")
    logger.info(f"- Total Employees: {len(employees)}")
    logger.info(f"- Policy Documents: {len(policies)}")
    logger.info(
        f"- Integration Mode: {'Fake / In-Memory (USE_FAKE_INTEGRATIONS=true)' if settings.USE_FAKE_INTEGRATIONS else 'Live'}"
    )
    logger.info("--------------------------------------------------")


async def async_main() -> None:
    await seed_database()
    print_synthetic_metadata_summary()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
