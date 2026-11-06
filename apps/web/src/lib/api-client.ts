import type {
  CompanyProfile,
  Employee,
  HealthResponse,
  PolicyDocument,
} from "@agentic-hr/contracts";

export type { CompanyProfile, Employee, HealthResponse, PolicyDocument };

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE_URL}/api/health`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch health: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchCompanyProfile(): Promise<CompanyProfile> {
  const res = await fetch(`${API_BASE_URL}/api/synthetic/company`, {
    cache: "no-store",
    headers: {
      "X-Tenant-ID": "acme-tech-01",
    },
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch company profile: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchEmployees(): Promise<Employee[]> {
  const res = await fetch(`${API_BASE_URL}/api/synthetic/employees`, {
    cache: "no-store",
    headers: {
      "X-Tenant-ID": "acme-tech-01",
    },
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch employees: ${res.statusText}`);
  }
  return res.json();
}

export async function sendTestEmail(toEmail: string, subject: string, body: string) {
  const params = new URLSearchParams({
    to_email: toEmail,
    subject,
    body,
  });
  const res = await fetch(`${API_BASE_URL}/api/synthetic/test-email?${params.toString()}`, {
    method: "POST",
    headers: {
      "X-Tenant-ID": "acme-tech-01",
    },
  });
  return res.json();
}
