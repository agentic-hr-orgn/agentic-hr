from app.models.synthetic import CompanyProfile, EmployeeRecord, PolicyDocument


class SyntheticDataService:
    """Provides synthetic datasets for Stage 1 in compliance with docs/synthetic-data-plan.md."""

    @staticmethod
    def get_company_profile() -> CompanyProfile:
        policies = [
            PolicyDocument(
                title="Standard Leave Policy (English)",
                language="en",
                category="Leave & Attendance",
                summary="20 annual leaves, 10 sick leaves, 3-day advance notice required.",
            ),
            PolicyDocument(
                title="رخصت کی پالیسی (Urdu Leave Policy)",
                language="ur",
                category="Leave & Attendance",
                summary="سالانہ 20 چھٹیاں، 10 بیماری کی چھٹیاں، پیشگی اطلاع لازمی ہے۔",
            ),
            PolicyDocument(
                title="Attendance & Punctuality Policy",
                language="en",
                category="Attendance",
                summary="Standard working hours: 09:00 - 18:00 with 15 minutes grace period.",
            ),
            PolicyDocument(
                title="Employee Code of Conduct",
                language="en",
                category="Governance",
                summary="Professional ethics, anti-harassment, and confidentiality.",
            ),
            PolicyDocument(
                title="Remote Work Guidelines",
                language="en",
                category="Workplace",
                summary="Hybrid schedule: 2 days remote with manager pre-approval.",
            ),
            PolicyDocument(
                title="Information Security Protocol",
                language="en",
                category="Security",
                summary="Mandatory 2FA, data classification, and device encryption standards.",
            ),
        ]

        test_accounts = [
            "agentic.hr.test.ubaid.owner@gmail.com",
            "agentic.hr.test.ubaid.recruiter@gmail.com",
            "agentic.hr.test.ubaid.hm@gmail.com",
            "agentic.hr.test.ubaid.employee@gmail.com",
            "agentic.hr.test.ubaid.candidate1@gmail.com",
            "agentic.hr.test.ubaid.candidate2@gmail.com",
        ]

        return CompanyProfile(
            name="Acme Tech Solutions",
            domain="acmetech.internal",
            departments=["HR", "Engineering", "Sales", "Operations"],
            total_employees=40,
            test_accounts=test_accounts,
            policy_documents=policies,
        )

    @staticmethod
    def get_synthetic_employees() -> list[EmployeeRecord]:
        return [
            EmployeeRecord(
                employee_id="EMP-001",
                name="Ubaid Qureshi",
                email="agentic.hr.test.ubaid.owner@gmail.com",
                department="Executive",
                role="Owner & Managing Director",
                status="Active",
                is_test_account=True,
            ),
            EmployeeRecord(
                employee_id="EMP-002",
                name="Sarah Jenkins",
                email="agentic.hr.test.ubaid.recruiter@gmail.com",
                department="HR",
                role="Recruitment Lead",
                status="Active",
                is_test_account=True,
            ),
            EmployeeRecord(
                employee_id="EMP-003",
                name="Tariq Mahmood",
                email="agentic.hr.test.ubaid.hm@gmail.com",
                department="Engineering",
                role="Hiring Manager / Tech Lead",
                status="Active",
                is_test_account=True,
            ),
            EmployeeRecord(
                employee_id="EMP-004",
                name="Zainab Ali",
                email="agentic.hr.test.ubaid.employee@gmail.com",
                department="Engineering",
                role="Staff Software Engineer",
                status="Active",
                is_test_account=True,
            ),
            EmployeeRecord(
                employee_id="EMP-005",
                name="Hamza Khan",
                email="hamza.khan@acmetech.internal",
                department="Sales",
                role="Sales Executive",
                status="Active",
                is_test_account=False,
            ),
            EmployeeRecord(
                employee_id="EMP-006",
                name="Ayesha Siddiqua",
                email="ayesha.s@acmetech.internal",
                department="Operations",
                role="Operations Specialist",
                status="Active",
                is_test_account=False,
            ),
        ]
