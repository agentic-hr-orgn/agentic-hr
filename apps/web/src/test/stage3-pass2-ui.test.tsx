import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { apiRequest } from "@/lib/api-request";
import { formatPKT } from "@/lib/format";
import { resolveRouteTitle } from "@/components/layout/header";
import ApprovalsPage from "@/app/(dashboard)/approvals/page";
import EmployeesPage from "@/app/(dashboard)/employees/page";
import * as approvalsHook from "@/hooks/use-approvals";
import * as backendStatusHook from "@/hooks/use-backend-status";
import * as currentUserHook from "@/hooks/use-current-user";
import * as departmentsHook from "@/hooks/use-departments";
import * as employeesHook from "@/hooks/use-employees";

vi.mock("@/hooks/use-approvals");
vi.mock("@/hooks/use-backend-status");
vi.mock("@/hooks/use-current-user");
vi.mock("@/hooks/use-departments");
vi.mock("@/hooks/use-employees");

describe("Stage 3 Pass 2 — Enterprise UI, Standby States & Helpers", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    vi.spyOn(backendStatusHook, "useBackendStatus").mockReturnValue({
      status: "standby",
      health: null,
      isLive: false,
      isStandby: true,
      isChecking: false,
      useFakeIntegrations: true,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    } as any);
  });

  it("apiRequest discriminates live, http_error ({error:{code,message}}), and offline cleanly", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ status: "ok" }),
    } as Response);
    const live = await apiRequest<{ status: string }>("http://localhost:8000/health");
    expect(live).toEqual({ kind: "live", status: 200, data: { status: "ok" } });

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({
        error: { code: "UNAUTHORIZED", message: "CONNECTOR_AUTH_EXPIRED: invalid_grant" },
      }),
    } as Response);
    const httpErr = await apiRequest("http://localhost:8000/api/v1/connectors");
    expect(httpErr.kind).toBe("http_error");
    if (httpErr.kind === "http_error") {
      expect(httpErr.status).toBe(401);
      expect(httpErr.code).toBe("UNAUTHORIZED");
      expect(httpErr.message).toBe("CONNECTOR_AUTH_EXPIRED: invalid_grant");
    }

    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const offline = await apiRequest("http://localhost:8000/api/v1/connectors");
    expect(offline.kind).toBe("offline");
  });

  it("resolveRouteTitle resolves /approvals, /approvals/[id]/correct, and /directory accurately", () => {
    expect(resolveRouteTitle("/approvals").title).toBe("Approvals Inbox");
    expect(resolveRouteTitle("/approvals/123/correct").title).toBe("Approval Correction");
    expect(resolveRouteTitle("/directory").title).toBe("Employee Directory");
    expect(resolveRouteTitle("/").title).toBe("Dashboard Overview");
  });

  it("formatPKT formats UTC timestamps deterministically in Asia/Karachi (+05:00)", () => {
    const formatted = formatPKT("2026-11-20T10:00:00Z");
    expect(formatted).toContain("15:00 PKT");
    expect(formatPKT(null)).toBe("—");
  });

  it("ApprovalsPage renders read-only standby panel with Retry and fabricates zero approvals when offline", () => {
    vi.spyOn(approvalsHook, "useApprovals").mockReturnValue({
      data: [],
      isLoading: false,
      error: new Error("OFFLINE: FastAPI backend is currently unreachable."),
      refetch: vi.fn(),
      isFetching: false,
    } as any);
    vi.spyOn(approvalsHook, "useApproveMutation").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(approvalsHook, "useRejectMutation").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(approvalsHook, "useRequestChangesMutation").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    render(<ApprovalsPage />);

    expect(screen.getByText(/Approvals Inbox in Read-Only Standby/i)).toBeInTheDocument();
    expect(screen.getByText(/Governance Inbox Unavailable \(Standby\)/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry Connection/i })).toBeInTheDocument();
    expect(screen.queryByText(/No approval requests found/i)).not.toBeInTheDocument();
  });

  it("ApprovalsPage separates live HTTP 403 auth/org errors from offline standby and avoids double periods", () => {
    vi.spyOn(backendStatusHook, "useBackendStatus").mockReturnValue({
      status: "live",
      health: null,
      isLive: true,
      isStandby: false,
      isChecking: false,
      useFakeIntegrations: true,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    vi.spyOn(approvalsHook, "useApprovals").mockReturnValue({
      data: [],
      isLoading: false,
      error: new Error(
        "No active organization selected. Please select an organization to proceed."
      ),
      refetch: vi.fn(),
      isFetching: false,
    } as any);
    vi.spyOn(approvalsHook, "useApproveMutation").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(approvalsHook, "useRejectMutation").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(approvalsHook, "useRequestChangesMutation").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    const { container } = render(<ApprovalsPage />);

    expect(
      screen.getAllByText(/Authentication \/ Organization Required/i).length
    ).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/Governance Inbox Unavailable \(Standby\)/i)).not.toBeInTheDocument();
    expect(container.textContent).not.toContain("proceed..");
  });

  it("EmployeesPage renders loading skeleton first, Reloading... feedback, and Last reloaded PKT timestamp", () => {
    vi.spyOn(departmentsHook, "useDepartments").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(employeesHook, "useCreateEmployee").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(employeesHook, "useEmployees").mockReturnValue({
      data: undefined,
      isLoading: true,
      isFetching: true,
      dataUpdatedAt: 0,
      error: null,
      refetch: vi.fn(),
    } as any);

    const { rerender } = render(<EmployeesPage />);
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(screen.getByRole("button", { name: /Reloading\.\.\./i })).toBeDisabled();

    vi.spyOn(employeesHook, "useEmployees").mockReturnValue({
      data: {
        items: [
          {
            id: "emp-001",
            tenant_id: "tenant-01",
            employee_code: "EMP-001",
            full_name: "Ubaid Qureshi (Owner)",
            email: "agentic.hr.test.ubaid.owner@gmail.com",
            department_name: "Executive",
            job_title: "Company Owner & Admin",
            employment_type: "full_time",
            join_date: "2024-01-01",
            status: "active",
            created_at: "2026-11-20T10:00:00+05:00",
            updated_at: "2026-11-20T10:00:00+05:00",
          },
        ],
        total: 1,
        page: 1,
        page_size: 15,
        total_pages: 1,
      },
      isLoading: false,
      isFetching: false,
      dataUpdatedAt: Date.now(),
      error: null,
      refetch: vi.fn(),
    } as any);

    rerender(<EmployeesPage />);
    expect(screen.getByText("Standby Sample")).toBeInTheDocument();
    expect(screen.getByText("Ubaid Qureshi (Owner)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reload Postgres Data/i })).not.toBeDisabled();
    expect(screen.getByTestId("last-reloaded-pkt")).toHaveTextContent(/Last reloaded:.*PKT/);
  });
});
