import { describe, it, expect, vi, beforeEach } from "vitest";
import { ApiHttpError } from "@/lib/api-request";
import { sendTestEmail } from "@/lib/api-client";

describe("Dashboard 'Test Fake Email' Integration & In-Memory Fallback", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("calls POST /api/v1/connectors/email/send with Idempotency-Key and dev auth headers", async () => {
    const mockBackendResponse = {
      status: "success",
      message_id: "fake_msg_live_12345",
      sent_message_id: "550e8400-e29b-41d4-a716-446655440000",
      recipient: "agentic.hr.test.ubaid.owner@gmail.com",
      fake: true,
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => mockBackendResponse,
    } as Response);

    const res = await sendTestEmail(
      "agentic.hr.test.ubaid.owner@gmail.com",
      "Test Subject",
      "Test Body",
      "mock_token_abc"
    );

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, options] = fetchSpy.mock.calls[0];
    expect(url).toContain("/api/v1/connectors/email/send");
    expect(options?.method).toBe("POST");

    const headers = options?.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers["Authorization"]).toBe("Bearer mock_token_abc");
    expect(headers["Idempotency-Key"]).toMatch(/^email-test-\d+-[a-z0-9]+$/);
    expect(headers["X-Tenant-ID"]).toBe("acme-tech-01");

    const body = JSON.parse(options?.body as string);
    expect(body.to_email).toBe("agentic.hr.test.ubaid.owner@gmail.com");
    expect(body.template_name).toBe("system_test_email");
    expect(body.force_retry).toBe(true);
    expect(body.template_variables.integration_name).toBe("Google Workspace Gmail (ADR 006)");

    expect(res).toEqual({
      source: "live",
      ...mockBackendResponse,
    });
  });

  it("gracefully falls back to standby simulator response when backend is offline (Failed to fetch)", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const res = await sendTestEmail();

    expect(res).toBeDefined();
    expect(res.source).toBe("standby-simulator");
    expect(res.status).toBe("sent");
    expect(res.provider).toBe("FakeGmailService");
    expect(res.recipient).toBe("agentic.hr.test.ubaid.owner@gmail.com");
    expect(res.template).toBe("system_test_email");
    expect(res.message_id).toMatch(/^fake_msg_\d+_[a-z0-9]+$/);
    expect(typeof res.timestamp).toBe("string");
    expect(Number.isNaN(Date.parse(res.timestamp!))).toBe(false);
  });

  it("throws ApiHttpError instead of fabricating success when backend returns HTTP 403 / 503", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: false,
      status: 403,
      statusText: "Forbidden",
      json: async () => ({
        error: {
          code: "FORBIDDEN",
          message: "CONNECTOR_READ_ONLY: Connector is configured in read-only mode.",
        },
      }),
    } as Response);

    await expect(sendTestEmail("agentic.hr.test.ubaid.owner@gmail.com")).rejects.toMatchObject({
      name: "ApiHttpError",
      status: 403,
      code: "FORBIDDEN",
      message: "CONNECTOR_READ_ONLY: Connector is configured in read-only mode.",
    });

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      json: async () => ({
        error: {
          code: "SERVICE_UNAVAILABLE",
          message: "CONNECTOR_UNAVAILABLE: Upstream unavailable",
        },
      }),
    } as Response);

    await expect(sendTestEmail("agentic.hr.test.ubaid.owner@gmail.com")).rejects.toBeInstanceOf(
      ApiHttpError
    );
  });
});
