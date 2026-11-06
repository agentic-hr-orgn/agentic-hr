from pydantic import BaseModel


class EmployeeRecord(BaseModel):
    employee_id: str
    name: str
    email: str
    department: str
    role: str
    status: str = "Active"
    is_test_account: bool = False


class PolicyDocument(BaseModel):
    title: str
    language: str
    category: str
    summary: str


class CompanyProfile(BaseModel):
    name: str = "Acme Tech Solutions"
    domain: str = "acmetech.internal"
    departments: list[str] = ["HR", "Engineering", "Sales", "Operations"]
    total_employees: int = 40
    test_accounts: list[str]
    policy_documents: list[PolicyDocument]
