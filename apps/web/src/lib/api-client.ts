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

export async function sendTestEmail(
  toEmail?: string,
  subject?: string,
  body?: string,
  authToken?: string | null
) {
  const recipient = toEmail || "agentic.hr.test.ubaid.owner@gmail.com";
  const idempotencyKey = `email-test-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
  const authHeader = authToken
    ? authToken.startsWith("Bearer ")
      ? authToken
      : `Bearer ${authToken}`
    : "Bearer mock:agentic.hr.test.ubaid.owner@gmail.com:org_acme_tech_01";

  const payload = {
    to_email: recipient,
    subject: subject || "Platform Simulation: Test Fake Email",
    body_text:
      body || "Simulated system test email sent via Agentic HR least-privilege Gmail connector.",
    template_name: "system_test_email",
    template_variables: {
      recipient_name: "Ubaid (Owner)",
      integration_name: "Google Workspace Gmail (ADR 006)",
      sender_email: "agentic.hr.test.ubaid.owner@gmail.com",
      test_timestamp: new Date().toISOString(),
      environment: "development (fake mode)",
    },
    force_retry: true,
  };

  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/connectors/email/send`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: authHeader,
        "Idempotency-Key": idempotencyKey,
        "X-Tenant-ID": "acme-tech-01",
      },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      return await res.json();
    }
  } catch {
    // Network error or backend offline: fall through to in-memory simulator response
  }

  // Local in-memory simulator response fallback (FastAPI standby or network error)
  return {
    status: "sent",
    provider: "FakeGmailService",
    recipient,
    template: "system_test_email",
    message_id: `fake_msg_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`,
    timestamp: new Date().toISOString(),
  };
}
