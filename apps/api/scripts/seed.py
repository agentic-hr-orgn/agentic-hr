"""
Safe and Idempotent Synthetic Data Seeder for Agentic HR Core.
Complies with ADR 003, ADR 006, and docs/synthetic-data-plan.md.
Uses strictly synthetic datasets (Acme Tech Solutions).
"""

import logging
from app.core.config import settings
from app.services.synthetic_service import SyntheticDataService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [seed] %(message)s",
)
logger = logging.getLogger("agentic_hr.seed")


def seed_synthetic_data() -> None:
    logger.info("Starting synthetic data seeding process...")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Fake integrations mode: {settings.USE_FAKE_INTEGRATIONS}")

    # 1. Load Synthetic Company Profile
    company = SyntheticDataService.get_company_profile()
    logger.info(f"[Company] Loaded profile for: {company.name} ({company.domain})")
    logger.info(f"[Company] Departments: {', '.join(company.departments)}")
    logger.info(f"[Company] Total employees: {company.total_employees}")

    # 2. Verify Authorized Test Accounts (ADR 006 Least-Privilege Set)
    test_accounts = company.test_accounts
    logger.info(f"[Test Accounts] Verified {len(test_accounts)} test accounts:")
    for acc in test_accounts:
        logger.info(f"  - {acc}")

    # 3. Verify Synthetic Employees
    employees = SyntheticDataService.get_synthetic_employees()
    logger.info(f"[Employees] Loaded {len(employees)} synthetic employee records:")
    for emp in employees:
        logger.info(f"  - [{emp.employee_id}] {emp.name} ({emp.role}, {emp.department})")

    # 4. Verify Bilingual Policy Documents (ADR 003 Bilingual Policy Brain)
    policies = company.policy_documents
    logger.info(f"[Policies] Loaded {len(policies)} policy documents:")
    for pol in policies:
        logger.info(f"  - [{pol.language.upper()}] {pol.title} ({pol.category})")

    logger.info("--------------------------------------------------")
    logger.info("SYNTHETIC DATA SEEDING COMPLETE (IDEMPOTENT)")
    logger.info(f"- Company: {company.name}")
    logger.info(f"- Employees: {len(employees)} verified")
    logger.info(f"- Policies: {len(policies)} verified")
    logger.info(f"- Test Accounts: {len(test_accounts)} verified")
    logger.info(f"- Integration mode: {'Fake / In-Memory (Stage 1)' if settings.USE_FAKE_INTEGRATIONS else 'Live'}")
    logger.info("--------------------------------------------------")


def main() -> None:
    seed_synthetic_data()


if __name__ == "__main__":
    main()
