"""
Safe and Idempotent Synthetic Data Seeder for Agentic HR Core.
Complies with ADR 003, ADR 006, and docs/synthetic-data-plan.md.
Seeds PostgreSQL database with two companies (Acme Tech Solutions & Globex Corp)
and authorized test user accounts across multi-tenant memberships.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.approval import Approval
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

# Administrative session for synthetic provisioning
migration_url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL
seed_engine = create_async_engine(migration_url, poolclass=NullPool)
SeedSessionLocal = async_sessionmaker(
    bind=seed_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def seed_database() -> None:
    """Idempotently seeds PostgreSQL database with roles, companies, users, and memberships."""
    logger.info("Initializing PostgreSQL database synthetic seeder...")

    async with SeedSessionLocal() as session:
        # 1. Verify / Seed Roles
        logger.info("Verifying system roles in PostgreSQL...")
        role_definitions = [
            (
                "owner",
                "Owner",
                "Full administrative control over tenant settings, billing, and memberships",
            ),
            (
                "manager",
                "Manager",
                "Operational manager with candidate review, interview scheduling, and team oversight",
            ),
            (
                "employee",
                "Employee",
                "Standard staff access for leave requests, policy RAG queries, and personal directory",
            ),
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
            (
                "agentic.hr.test.ubaid.recruiter@gmail.com",
                "user_test_recruiter_02",
                "Ubaid Recruiter",
            ),
            ("agentic.hr.test.ubaid.hm@gmail.com", "user_test_hm_03", "Ubaid Hiring Manager"),
            (
                "agentic.hr.test.ubaid.employee@gmail.com",
                "user_test_employee_04",
                "Ubaid Staff Employee",
            ),
            (
                "agentic.hr.test.ubaid.candidate1@gmail.com",
                "user_test_candidate1_05",
                "Ubaid Candidate One",
            ),
            (
                "agentic.hr.test.ubaid.candidate2@gmail.com",
                "user_test_candidate2_06",
                "Ubaid Cross-Company Candidate Two",
            ),
            (
                "agentic.hr.test.ubaid.globex_owner@gmail.com",
                "user_test_globex_owner_07",
                "Globex Owner",
            ),
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
            (
                "agentic.hr.test.ubaid.recruiter@gmail.com",
                "org_acme_tech_01",
                "manager",
                True,
                False,
            ),
            ("agentic.hr.test.ubaid.hm@gmail.com", "org_acme_tech_01", "manager", False, False),
            (
                "agentic.hr.test.ubaid.employee@gmail.com",
                "org_acme_tech_01",
                "employee",
                False,
                False,
            ),
            # Candidate 1 belongs to Globex Corporation
            (
                "agentic.hr.test.ubaid.candidate1@gmail.com",
                "org_globex_corp_02",
                "employee",
                False,
                False,
            ),
            # Candidate 2 belongs to BOTH Acme Tech Solutions AND Globex Corporation (Multi-Tenancy Proof)
            (
                "agentic.hr.test.ubaid.candidate2@gmail.com",
                "org_acme_tech_01",
                "employee",
                False,
                False,
            ),
            (
                "agentic.hr.test.ubaid.candidate2@gmail.com",
                "org_globex_corp_02",
                "manager",
                True,
                False,
            ),
            # Globex Corporation Owner (Company B Owner for symmetric isolation & RBAC testing)
            (
                "agentic.hr.test.ubaid.globex_owner@gmail.com",
                "org_globex_corp_02",
                "owner",
                True,
                True,
            ),
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

        # 5. Seed Synthetic Approvals for Acme Tech Solutions & Globex Corp
        logger.info("Seeding synthetic human approval requests...")
        from datetime import UTC, datetime, timedelta

        acme_tenant = tenants["org_acme_tech_01"]
        globex_tenant = tenants["org_globex_corp_02"]

        owner_user = users["agentic.hr.test.ubaid.owner@gmail.com"]
        recruiter_user = users["agentic.hr.test.ubaid.recruiter@gmail.com"]
        hm_user = users["agentic.hr.test.ubaid.hm@gmail.com"]
        employee_user = users["agentic.hr.test.ubaid.employee@gmail.com"]
        cand1_user = users["agentic.hr.test.ubaid.candidate1@gmail.com"]

        approvals_data = [
            # Acme Tech - Pending Leave Request
            {
                "tenant_id": acme_tenant.id,
                "approval_type": "leave_request",
                "requester_id": employee_user.id,
                "approver_role": "manager",
                "status": "pending",
                "payload": {
                    "leave_type": "casual",
                    "start_date": "2026-11-15",
                    "end_date": "2026-11-17",
                    "days_count": 3.0,
                    "reason": "Family gathering and personal commitments.",
                },
                "decided_by": None,
                "decision_reason": None,
                "decided_at": None,
            },
            # Acme Tech - Pending Candidate Offer
            {
                "tenant_id": acme_tenant.id,
                "approval_type": "candidate_offer",
                "requester_id": recruiter_user.id,
                "approver_role": "owner",
                "status": "pending",
                "payload": {
                    "candidate_name": "Zubair Khan",
                    "candidate_email": "zubair.khan.dev@example.com",
                    "position": "Lead AI Engineer",
                    "offered_salary_pkr": 450000,
                    "start_date": "2026-12-01",
                },
                "decided_by": None,
                "decision_reason": None,
                "decided_at": None,
            },
            # Acme Tech - Approved Payroll Override
            {
                "tenant_id": acme_tenant.id,
                "approval_type": "payroll_override",
                "requester_id": hm_user.id,
                "approver_role": "owner",
                "status": "approved",
                "payload": {
                    "employee_id": str(employee_user.id),
                    "employee_name": employee_user.name,
                    "bonus_pkr": 50000,
                    "deduction_pkr": None,
                    "justification": "Exceptional delivery of Q3 performance milestones.",
                },
                "decided_by": owner_user.id,
                "decision_reason": "Approved on basis of verified project impact.",
                "decided_at": datetime.now(UTC) - timedelta(days=2),
            },
            # Acme Tech - Rejected Policy Exception
            {
                "tenant_id": acme_tenant.id,
                "approval_type": "policy_exception",
                "requester_id": employee_user.id,
                "approver_role": "hr_head",
                "status": "rejected",
                "payload": {
                    "policy_name": "Remote Work Policy",
                    "exception_description": "Requesting 60 consecutive days full-time remote arrangement.",
                    "impact_assessment": "Team velocity maintained via async communication.",
                },
                "decided_by": recruiter_user.id,
                "decision_reason": "Exceeds standard 30-day remote work policy window.",
                "decided_at": datetime.now(UTC) - timedelta(days=1),
            },
            # Globex Corp - Pending Leave Request
            {
                "tenant_id": globex_tenant.id,
                "approval_type": "leave_request",
                "requester_id": cand1_user.id,
                "approver_role": "manager",
                "status": "pending",
                "payload": {
                    "leave_type": "annual",
                    "start_date": "2026-11-20",
                    "end_date": "2026-11-25",
                    "days_count": 5.0,
                    "reason": "Annual planned vacation.",
                },
                "decided_by": None,
                "decision_reason": None,
                "decided_at": None,
            },
        ]

        for item in approvals_data:
            stmt = select(Approval).where(
                Approval.tenant_id == item["tenant_id"],
                Approval.approval_type == item["approval_type"],
                Approval.requester_id == item["requester_id"],
            )
            existing = (await session.execute(stmt)).scalars().first()
            if not existing:
                appr = Approval(**item)
                session.add(appr)
                logger.info(
                    f"[Approval Created] {item['approval_type']} in tenant {item['tenant_id']} (Status: {item['status']})"
                )
            else:
                logger.info(
                    f"[Approval Exists] {item['approval_type']} in tenant {item['tenant_id']}"
                )

        await session.commit()
        logger.info("Human approval synthetic seeding completed successfully.")


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
    try:
        await seed_database()
        print_synthetic_metadata_summary()
    finally:
        await seed_engine.dispose()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
