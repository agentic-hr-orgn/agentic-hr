"""
Safe and Idempotent Synthetic Data Seeder for Agentic HR Core.
Complies with ADR 003, ADR 006, and docs/synthetic-data-plan.md.
Seeds PostgreSQL database with two companies (Acme Tech Solutions & Globex Corp),
authorized test user accounts across multi-tenant memberships, and Stage 4
4 departments + 40 synthetic employees with encrypted PII and sample expiring documents.
"""

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.timezones import to_karachi
from app.models.approval import Approval
from app.models.department import Department
from app.models.employee import Employee
from app.models.employee_document import EmployeeDocument
from app.models.membership import Membership
from app.models.role import Role
from app.models.tenant import set_tenant_context
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

# ------------------------------------------------------------------------------
# Stage 4: Deterministic 4 Departments & 40 Employees Specification for Acme Tech
# ------------------------------------------------------------------------------
ACME_DEPARTMENTS_SPEC: list[dict[str, str]] = [
    {
        "name": "Engineering",
        "code": "ENG",
        "description": "Core platform, AI agents, backend, frontend, and cloud infrastructure.",
        "head_code": "EMP-003",
    },
    {
        "name": "Human Resources",
        "code": "HR",
        "description": "Talent acquisition, people operations, onboarding, and policy compliance.",
        "head_code": "EMP-002",
    },
    {
        "name": "Product & Design",
        "code": "PROD",
        "description": "Product management, UX research, design systems, and customer discovery.",
        "head_code": "EMP-004",
    },
    {
        "name": "Finance & Operations",
        "code": "FINOPS",
        "description": "Corporate finance, payroll governance, legal, and workplace operations.",
        "head_code": "EMP-005",
    },
]


def _build_acme_40_employees_spec() -> list[dict[str, Any]]:
    """
    Returns deterministic 40-employee specification with a 4-level manager_id hierarchy:
    - Level 0 (1): EMP-001 (CEO / Managing Director, manager_code=None)
    - Level 1 (4): EMP-002..EMP-005 (Department Heads reporting to EMP-001)
    - Level 2 (8): EMP-006..EMP-013 (Team Leads / Managers reporting to Level 1 Heads)
    - Level 3 (27): EMP-014..EMP-040 (ICs reporting to Level 2 Managers)
    Department distribution:
    - Engineering: 16
    - Human Resources: 6
    - Product & Design: 9
    - Finance & Operations: 9
    Total = 40 employees.
    """
    raw_rows: list[tuple[str, str, str, str, str | None, str, str, str, date]] = [
        # Level 0: Root CEO (1)
        (
            "EMP-001",
            "Ubaid Qureshi",
            "agentic.hr.test.ubaid.owner@gmail.com",
            "Engineering",
            None,
            "Chief Executive Officer & Managing Director",
            "full_time",
            "active",
            date(2022, 1, 10),
        ),
        # Level 1: Department Heads reporting to EMP-001 (4)
        (
            "EMP-002",
            "Sarah Jenkins",
            "agentic.hr.test.ubaid.recruiter@gmail.com",
            "Human Resources",
            "EMP-001",
            "VP of People & Talent",
            "full_time",
            "active",
            date(2022, 2, 1),
        ),
        (
            "EMP-003",
            "Tariq Mahmood",
            "agentic.hr.test.ubaid.hm@gmail.com",
            "Engineering",
            "EMP-001",
            "VP of Engineering",
            "full_time",
            "active",
            date(2022, 2, 15),
        ),
        (
            "EMP-004",
            "Sana Farooq",
            "sana.farooq@acmetech.internal",
            "Product & Design",
            "EMP-001",
            "VP of Product & Design",
            "full_time",
            "active",
            date(2022, 3, 1),
        ),
        (
            "EMP-005",
            "Kamran Sheikh",
            "kamran.sheikh@acmetech.internal",
            "Finance & Operations",
            "EMP-001",
            "VP of Finance & Operations",
            "full_time",
            "active",
            date(2022, 3, 15),
        ),
        # Level 2: Team Leads / Managers reporting to Level 1 Department Heads (8)
        (
            "EMP-006",
            "Zainab Ali",
            "agentic.hr.test.ubaid.employee@gmail.com",
            "Engineering",
            "EMP-003",
            "Staff Software Engineer & Backend Lead",
            "full_time",
            "active",
            date(2022, 5, 10),
        ),
        (
            "EMP-007",
            "Bilal Ahmed",
            "bilal.ahmed@acmetech.internal",
            "Engineering",
            "EMP-003",
            "Engineering Manager - Frontend & Platform",
            "full_time",
            "active",
            date(2022, 6, 1),
        ),
        (
            "EMP-008",
            "Usman Raza",
            "usman.raza@acmetech.internal",
            "Engineering",
            "EMP-003",
            "Engineering Manager - AI & Data Systems",
            "full_time",
            "active",
            date(2022, 7, 1),
        ),
        (
            "EMP-009",
            "Hina Malik",
            "hina.malik@acmetech.internal",
            "Human Resources",
            "EMP-002",
            "HR Operations & Compliance Manager",
            "full_time",
            "active",
            date(2022, 8, 1),
        ),
        (
            "EMP-010",
            "Omar Siddiqui",
            "omar.siddiqui@acmetech.internal",
            "Product & Design",
            "EMP-004",
            "Group Product Manager",
            "full_time",
            "active",
            date(2022, 9, 1),
        ),
        (
            "EMP-011",
            "Nadia Hussain",
            "nadia.hussain@acmetech.internal",
            "Product & Design",
            "EMP-004",
            "Head of UX & Design Systems",
            "full_time",
            "active",
            date(2022, 10, 1),
        ),
        (
            "EMP-012",
            "Faisal Mehmood",
            "faisal.mehmood@acmetech.internal",
            "Finance & Operations",
            "EMP-005",
            "Senior Finance & Payroll Manager",
            "full_time",
            "active",
            date(2022, 11, 1),
        ),
        (
            "EMP-013",
            "Ayesha Siddiqua",
            "ayesha.s@acmetech.internal",
            "Finance & Operations",
            "EMP-005",
            "Workplace Operations Manager",
            "full_time",
            "active",
            date(2022, 12, 1),
        ),
        # Level 3: Individual Contributors reporting to Level 2 Managers (27)
        # Engineering ICs (11 -> total Engineering = 1 + 1 + 3 + 11 = 16)
        (
            "EMP-014",
            "Hamza Tariq",
            "hamza.tariq@acmetech.internal",
            "Engineering",
            "EMP-006",
            "Senior Backend Engineer",
            "full_time",
            "active",
            date(2023, 1, 15),
        ),
        (
            "EMP-015",
            "Mariam Nawaz",
            "mariam.nawaz@acmetech.internal",
            "Engineering",
            "EMP-006",
            "Backend Software Engineer",
            "full_time",
            "active",
            date(2023, 2, 10),
        ),
        (
            "EMP-016",
            "Saad Qamar",
            "saad.qamar@acmetech.internal",
            "Engineering",
            "EMP-006",
            "Distributed Systems Engineer",
            "full_time",
            "active",
            date(2023, 3, 5),
        ),
        (
            "EMP-017",
            "Anum Zahra",
            "anum.zahra@acmetech.internal",
            "Engineering",
            "EMP-006",
            "Site Reliability Engineer",
            "full_time",
            "active",
            date(2023, 4, 12),
        ),
        (
            "EMP-018",
            "Danish Javed",
            "danish.javed@acmetech.internal",
            "Engineering",
            "EMP-007",
            "Senior Frontend Engineer",
            "full_time",
            "active",
            date(2023, 5, 1),
        ),
        (
            "EMP-019",
            "Rabia Iqbal",
            "rabia.iqbal@acmetech.internal",
            "Engineering",
            "EMP-007",
            "Full Stack TypeScript Engineer",
            "full_time",
            "active",
            date(2023, 6, 15),
        ),
        (
            "EMP-020",
            "Fahad Mustafa",
            "fahad.mustafa@acmetech.internal",
            "Engineering",
            "EMP-007",
            "UI Accessibility Engineer",
            "contract",
            "active",
            date(2023, 7, 20),
        ),
        (
            "EMP-021",
            "Kiran Shah",
            "kiran.shah@acmetech.internal",
            "Engineering",
            "EMP-007",
            "QA Automation Engineer",
            "full_time",
            "on_leave",
            date(2023, 8, 10),
        ),
        (
            "EMP-022",
            "Zohaib Hassan",
            "zohaib.hassan@acmetech.internal",
            "Engineering",
            "EMP-008",
            "Applied AI Engineer",
            "full_time",
            "active",
            date(2023, 9, 1),
        ),
        (
            "EMP-023",
            "Amna Baig",
            "amna.baig@acmetech.internal",
            "Engineering",
            "EMP-008",
            "NLP & RAG Research Engineer",
            "full_time",
            "active",
            date(2023, 10, 10),
        ),
        (
            "EMP-024",
            "Taimoor Lodhi",
            "taimoor.lodhi@acmetech.internal",
            "Engineering",
            "EMP-008",
            "AI Engineering Intern",
            "intern",
            "active",
            date(2024, 6, 1),
        ),
        # Human Resources ICs (4 -> total HR = 1 + 1 + 4 = 6)
        (
            "EMP-025",
            "Mehwish Hayat",
            "mehwish.hayat@acmetech.internal",
            "Human Resources",
            "EMP-009",
            "Senior Technical Recruiter",
            "full_time",
            "active",
            date(2023, 2, 1),
        ),
        (
            "EMP-026",
            "Waqas Akram",
            "waqas.akram@acmetech.internal",
            "Human Resources",
            "EMP-009",
            "People Partner & L&D Specialist",
            "full_time",
            "active",
            date(2023, 5, 18),
        ),
        (
            "EMP-027",
            "Sadia Bokhari",
            "sadia.bokhari@acmetech.internal",
            "Human Resources",
            "EMP-009",
            "HR Coordinator",
            "full_time",
            "active",
            date(2023, 11, 1),
        ),
        (
            "EMP-028",
            "Arslan Gill",
            "arslan.gill@acmetech.internal",
            "Human Resources",
            "EMP-009",
            "Talent Sourcer",
            "part_time",
            "active",
            date(2024, 1, 15),
        ),
        # Product & Design ICs (6 -> total Product & Design = 1 + 2 + 6 = 9)
        (
            "EMP-029",
            "Mahnoor Baloch",
            "mahnoor.baloch@acmetech.internal",
            "Product & Design",
            "EMP-010",
            "Senior Product Manager - Workflows",
            "full_time",
            "active",
            date(2023, 3, 20),
        ),
        (
            "EMP-030",
            "Shehryar Munawar",
            "shehryar.munawar@acmetech.internal",
            "Product & Design",
            "EMP-010",
            "Product Manager - Integrations",
            "full_time",
            "active",
            date(2023, 6, 10),
        ),
        (
            "EMP-031",
            "Yumna Zaidi",
            "yumna.zaidi@acmetech.internal",
            "Product & Design",
            "EMP-010",
            "Product Data Analyst",
            "full_time",
            "active",
            date(2023, 9, 15),
        ),
        (
            "EMP-032",
            "Ahad Raza",
            "ahad.raza@acmetech.internal",
            "Product & Design",
            "EMP-011",
            "Senior Product Designer",
            "full_time",
            "active",
            date(2023, 4, 1),
        ),
        (
            "EMP-033",
            "Sajal Aly",
            "sajal.aly@acmetech.internal",
            "Product & Design",
            "EMP-011",
            "UX Researcher",
            "full_time",
            "active",
            date(2023, 8, 22),
        ),
        (
            "EMP-034",
            "Haris Waheed",
            "haris.waheed@acmetech.internal",
            "Product & Design",
            "EMP-011",
            "Visual & Interaction Designer",
            "contract",
            "active",
            date(2024, 2, 1),
        ),
        # Finance & Operations ICs (6 -> total Finance & Operations = 1 + 2 + 6 = 9)
        (
            "EMP-035",
            "Salman Shahid",
            "salman.shahid@acmetech.internal",
            "Finance & Operations",
            "EMP-012",
            "Senior Payroll & Tax Specialist",
            "full_time",
            "active",
            date(2023, 1, 25),
        ),
        (
            "EMP-036",
            "Bushra Ansari",
            "bushra.ansari@acmetech.internal",
            "Finance & Operations",
            "EMP-012",
            "Corporate Accountant",
            "full_time",
            "active",
            date(2023, 5, 12),
        ),
        (
            "EMP-037",
            "Javed Sheikh",
            "javed.sheikh@acmetech.internal",
            "Finance & Operations",
            "EMP-012",
            "Financial Compliance Analyst",
            "full_time",
            "active",
            date(2023, 10, 5),
        ),
        (
            "EMP-038",
            "Hamza Khan",
            "hamza.khan@acmetech.internal",
            "Finance & Operations",
            "EMP-013",
            "Vendor & Procurement Specialist",
            "full_time",
            "active",
            date(2023, 3, 14),
        ),
        (
            "EMP-039",
            "Sanam Saeed",
            "sanam.saeed@acmetech.internal",
            "Finance & Operations",
            "EMP-013",
            "IT & Security Operations Specialist",
            "full_time",
            "active",
            date(2023, 7, 8),
        ),
        (
            "EMP-040",
            "Imran Ashraf",
            "imran.ashraf@acmetech.internal",
            "Finance & Operations",
            "EMP-013",
            "Workplace Experience Associate",
            "full_time",
            "invited",
            date(2024, 4, 1),
        ),
    ]

    result: list[dict[str, Any]] = []
    for idx, (
        emp_code,
        full_name,
        email,
        dept_name,
        manager_code,
        job_title,
        emp_type,
        status,
        join_date,
    ) in enumerate(raw_rows, start=1):
        check_digit = (idx % 9) + 1
        synthetic_cnic = f"42101-{1000000 + idx:07d}-{check_digit}"
        synthetic_iban = f"PK36ACME0001{idx:012d}"
        synthetic_phone = f"+92-300-{2000000 + idx:07d}"
        result.append(
            {
                "employee_code": emp_code,
                "full_name": full_name,
                "email": email,
                "phone": synthetic_phone,
                "department_name": dept_name,
                "manager_code": manager_code,
                "job_title": job_title,
                "employment_type": emp_type,
                "status": status,
                "join_date": join_date,
                "synthetic_cnic": synthetic_cnic,
                "synthetic_iban": synthetic_iban,
            }
        )
    return result


async def seed_acme_stage4_data(
    session: AsyncSession,
    acme_tenant: Tenant,
    users: dict[str, User],
) -> dict[str, int]:
    """
    Idempotently seeds Acme Tech Solutions with:
    - 4 departments (Engineering, Human Resources, Product & Design, Finance & Operations)
    - 40 synthetic employees with multi-level manager_id hierarchy and AES-256-GCM encrypted CNIC/IBAN
    - Sample employee documents including documents expiring in 30, 7, and 1 days (in Asia/Karachi).
    """
    await set_tenant_context(session, str(acme_tenant.id))

    # 1. Upsert 4 Departments (initially without head_employee_id to avoid circular insert ordering)
    departments_by_name: dict[str, Department] = {}
    for dept_spec in ACME_DEPARTMENTS_SPEC:
        stmt = select(Department).where(
            Department.tenant_id == acme_tenant.id,
            func.lower(Department.name) == dept_spec["name"].lower(),
        )
        dept = (await session.execute(stmt)).scalar_one_or_none()
        if not dept:
            dept = Department(
                tenant_id=acme_tenant.id,
                name=dept_spec["name"],
                code=dept_spec["code"],
                description=dept_spec["description"],
            )
            session.add(dept)
            await session.flush()
            logger.info(f"[Department Created] {dept.name} ({dept.code}) -> {dept.id}")
        else:
            dept.code = dept_spec["code"]
            dept.description = dept_spec["description"]
        departments_by_name[dept_spec["name"]] = dept

    await session.flush()

    # 2. Upsert 40 Employees (pass 1: base attributes + encrypted PII)
    employees_spec = _build_acme_40_employees_spec()
    employees_by_code: dict[str, Employee] = {}

    for emp_spec in employees_spec:
        email_lower = emp_spec["email"].lower()
        stmt = select(Employee).where(
            Employee.tenant_id == acme_tenant.id,
            func.lower(Employee.email) == email_lower,
        )
        emp = (await session.execute(stmt)).scalar_one_or_none()
        linked_user = users.get(emp_spec["email"])
        dept = departments_by_name[emp_spec["department_name"]]

        if not emp:
            emp_id = uuid.uuid4()
            emp = Employee(
                id=emp_id,
                tenant_id=acme_tenant.id,
                user_id=linked_user.id if linked_user else None,
                employee_code=emp_spec["employee_code"],
                full_name=emp_spec["full_name"],
                email=emp_spec["email"],
                phone=emp_spec["phone"],
                department_id=dept.id,
                job_title=emp_spec["job_title"],
                employment_type=emp_spec["employment_type"],
                join_date=emp_spec["join_date"],
                status=emp_spec["status"],
            )
            emp.set_cnic(emp_spec["synthetic_cnic"])
            emp.set_bank_account(emp_spec["synthetic_iban"])
            session.add(emp)
            await session.flush()
        else:
            emp.employee_code = emp_spec["employee_code"]
            emp.full_name = emp_spec["full_name"]
            emp.phone = emp_spec["phone"]
            emp.department_id = dept.id
            emp.job_title = emp_spec["job_title"]
            emp.employment_type = emp_spec["employment_type"]
            emp.join_date = emp_spec["join_date"]
            emp.status = emp_spec["status"]
            if linked_user and emp.user_id is None:
                emp.user_id = linked_user.id
            if not emp.cnic_encrypted:
                emp.set_cnic(emp_spec["synthetic_cnic"])
            if not emp.bank_account_encrypted:
                emp.set_bank_account(emp_spec["synthetic_iban"])

        employees_by_code[emp_spec["employee_code"]] = emp

    await session.flush()

    # 3. Wire multi-level manager_id hierarchy (pass 2) and department head_employee_id
    for emp_spec in employees_spec:
        emp = employees_by_code[emp_spec["employee_code"]]
        mgr_code = emp_spec["manager_code"]
        emp.manager_id = employees_by_code[mgr_code].id if mgr_code else None

    for dept_spec in ACME_DEPARTMENTS_SPEC:
        dept = departments_by_name[dept_spec["name"]]
        head_emp = employees_by_code.get(dept_spec["head_code"])
        if head_emp:
            dept.head_employee_id = head_emp.id

    await session.flush()

    # 4. Seed sample EmployeeDocuments (including docs expiring in 30, 7, and 1 days in Asia/Karachi)
    today_pkt = to_karachi(datetime.now(UTC)).date()
    owner_user = users.get("agentic.hr.test.ubaid.owner@gmail.com")
    uploader_id = owner_user.id if owner_user else None

    sample_docs_spec: list[dict[str, Any]] = [
        {
            "employee_code": "EMP-003",
            "document_type": "cnic",
            "title": "NADRA Smart CNIC - Tariq Mahmood (Expiring in 30 Days)",
            "file_name": "emp-003-cnic-scan.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 412800,
            "storage_provider": "local",
            "storage_key": "acme/employees/EMP-003/cnic-scan.pdf",
            "expiry_date": today_pkt + timedelta(days=30),
        },
        {
            "employee_code": "EMP-006",
            "document_type": "contract",
            "title": "Addendum & Security Clearance - Zainab Ali (Expiring in 7 Days)",
            "file_name": "emp-006-contract-addendum.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 628400,
            "storage_provider": "local",
            "storage_key": "acme/employees/EMP-006/contract-addendum.pdf",
            "expiry_date": today_pkt + timedelta(days=7),
        },
        {
            "employee_code": "EMP-007",
            "document_type": "certificate",
            "title": "ISO 27001 Lead Implementer Certificate - Bilal Ahmed (Expiring in 1 Day)",
            "file_name": "emp-007-iso27001-cert.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 318900,
            "storage_provider": "local",
            "storage_key": "acme/employees/EMP-007/iso27001-cert.pdf",
            "expiry_date": today_pkt + timedelta(days=1),
        },
        {
            "employee_code": "EMP-001",
            "document_type": "contract",
            "title": "Executive Employment Agreement - Ubaid Qureshi",
            "file_name": "emp-001-executive-agreement.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 854200,
            "storage_provider": "local",
            "storage_key": "acme/employees/EMP-001/executive-agreement.pdf",
            "expiry_date": None,
        },
        {
            "employee_code": "EMP-002",
            "document_type": "cnic",
            "title": "Identity Verification Document - Sarah Jenkins",
            "file_name": "emp-002-id-verification.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 512000,
            "storage_provider": "local",
            "storage_key": "acme/employees/EMP-002/id-verification.pdf",
            "expiry_date": today_pkt + timedelta(days=180),
        },
    ]

    docs_count = 0
    for doc_spec in sample_docs_spec:
        target_emp = employees_by_code[doc_spec["employee_code"]]
        stmt = select(EmployeeDocument).where(
            EmployeeDocument.tenant_id == acme_tenant.id,
            EmployeeDocument.employee_id == target_emp.id,
            EmployeeDocument.storage_key == doc_spec["storage_key"],
        )
        existing_doc = (await session.execute(stmt)).scalar_one_or_none()
        if not existing_doc:
            new_doc = EmployeeDocument(
                tenant_id=acme_tenant.id,
                employee_id=target_emp.id,
                document_type=doc_spec["document_type"],
                title=doc_spec["title"],
                file_name=doc_spec["file_name"],
                mime_type=doc_spec["mime_type"],
                size_bytes=doc_spec["size_bytes"],
                storage_provider=doc_spec["storage_provider"],
                storage_key=doc_spec["storage_key"],
                expiry_date=doc_spec["expiry_date"],
                uploaded_by=uploader_id,
            )
            session.add(new_doc)
        else:
            existing_doc.title = doc_spec["title"]
            existing_doc.expiry_date = doc_spec["expiry_date"]
        docs_count += 1

    await session.commit()
    logger.info(
        f"[Stage 4 Seed] Acme Tech Solutions seeded with {len(departments_by_name)} departments, "
        f"{len(employees_by_code)} employees, and {docs_count} employee documents."
    )
    return {
        "departments": len(departments_by_name),
        "employees": len(employees_by_code),
        "documents": docs_count,
    }


async def seed_database() -> None:
    """Idempotently seeds PostgreSQL database with roles, companies, users, memberships, and Stage 4 employees."""
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
            (
                "agentic.hr.test.ubaid.candidate1@gmail.com",
                "org_globex_corp_02",
                "employee",
                False,
                False,
            ),
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

        acme_tenant = tenants["org_acme_tech_01"]
        globex_tenant = tenants["org_globex_corp_02"]

        owner_user = users["agentic.hr.test.ubaid.owner@gmail.com"]
        recruiter_user = users["agentic.hr.test.ubaid.recruiter@gmail.com"]
        hm_user = users["agentic.hr.test.ubaid.hm@gmail.com"]
        employee_user = users["agentic.hr.test.ubaid.employee@gmail.com"]
        cand1_user = users["agentic.hr.test.ubaid.candidate1@gmail.com"]

        approvals_data = [
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
                    f"[Approval Created] {item['approval_type']} in tenant {item['tenant_id']} "
                    f"(Status: {item['status']})"
                )
            else:
                logger.info(
                    f"[Approval Exists] {item['approval_type']} in tenant {item['tenant_id']}"
                )

        await session.commit()
        logger.info("Human approval synthetic seeding completed successfully.")

        # 6. Stage 4: Seed 4 Departments, 40 Synthetic Employees (Encrypted PII) & Expiring Docs
        await seed_acme_stage4_data(session, acme_tenant, users)


def print_synthetic_metadata_summary() -> None:
    """Preserves Stage 1 in-memory synthetic service metadata verification."""
    company = SyntheticDataService.get_company_profile()
    employees = SyntheticDataService.get_synthetic_employees()
    policies = company.policy_documents

    logger.info("--------------------------------------------------")
    logger.info("SYNTHETIC DATA VERIFICATION SUMMARY (STAGE 4)")
    logger.info(f"- Company: {company.name} ({company.domain})")
    logger.info(f"- In-Memory Preview Employees: {len(employees)} | DB Seeded Employees: 40")
    logger.info(f"- Policy Documents: {len(policies)}")
    logger.info(
        f"- Integration Mode: "
        f"{'Fake / In-Memory (USE_FAKE_INTEGRATIONS=true)' if settings.USE_FAKE_INTEGRATIONS else 'Live'}"
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
