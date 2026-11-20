import type {
  CompanyProfile,
  Employee,
  HealthResponse,
  PolicyDocument,
} from "@agentic-hr/contracts";
import { ApiHttpError, apiRequest } from "@/lib/api-request";
import { formatBearerToken } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

export type { CompanyProfile, Employee, HealthResponse, PolicyDocument };

export interface TestEmailResult {
  source: "live" | "standby-simulator";
  status: string;
  provider?: string;
  recipient?: string;
  template?: string;
  message_id?: string;
  sent_message_id?: string;
  fake?: boolean;
  timestamp?: string;
  [key: string]: unknown;
}

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
): Promise<TestEmailResult> {
  const recipient = toEmail || "agentic.hr.test.ubaid.owner@gmail.com";
  const idempotencyKey = `email-test-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
  const authHeader = formatBearerToken(authToken);

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

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Idempotency-Key": idempotencyKey,
    "X-Tenant-ID": "acme-tech-01",
  };
  if (authHeader) {
    headers.Authorization = authHeader;
  }

  const res = await apiRequest<Record<string, unknown>>(
    `${API_BASE_URL}/api/v1/connectors/email/send`,
    {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    },
    "Email connector request failed"
  );

  if (res.kind === "live") {
    return {
      source: "live",
      status: String(res.data?.status ?? "success"),
      ...res.data,
    };
  }

  if (res.kind === "http_error") {
    throw new ApiHttpError(res.status, res.code, res.message, res.body);
  }

  // Local in-memory simulator response fallback ONLY when FastAPI is offline / unreachable
  return {
    source: "standby-simulator",
    status: "sent",
    provider: "FakeGmailService",
    recipient,
    template: "system_test_email",
    message_id: `fake_msg_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`,
    timestamp: new Date().toISOString(),
  };
}
