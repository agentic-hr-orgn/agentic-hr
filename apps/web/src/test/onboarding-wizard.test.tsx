import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import OnboardingPage from "@/app/(dashboard)/onboarding/page";
import { resolveRouteTitle } from "@/components/layout/header";
import * as backendStatusHook from "@/hooks/use-backend-status";
import * as connectorsHook from "@/hooks/use-connectors";
import * as currentUserHook from "@/hooks/use-current-user";
import * as departmentsHook from "@/hooks/use-departments";
import * as employeeImportHook from "@/hooks/use-employee-import";
import * as employeesHook from "@/hooks/use-employees";
import * as leaveHook from "@/hooks/use-leave";
import * as onboardingHook from "@/hooks/use-onboarding";
import * as policiesHook from "@/hooks/use-policies";
import { ApiHttpError } from "@/lib/api-request";
import type { ConnectorResponse, OnboardingState, ReadinessChecklist } from "@agentic-hr/contracts";

vi.mock("@/hooks/use-backend-status");
vi.mock("@/hooks/use-connectors");
vi.mock("@/hooks/use-current-user");
vi.mock("@/hooks/use-departments");
vi.mock("@/hooks/use-employee-import");
vi.mock("@/hooks/use-employees");
vi.mock("@/hooks/use-leave");
vi.mock("@/hooks/use-onboarding");
vi.mock("@/hooks/use-policies");

const BASE_CONNECTOR: ConnectorResponse = {
  id: "conn-01",
  tenant_id: "tenant-01",
  provider: "google",
  account_email: "agentic.hr.test.ubaid.owner@gmail.com",
  scopes: [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/calendar.events",
  ],
  status: "connected",
  is_read_only: false,
  jobs_paused: false,
  error_message: null,
  created_at: "2026-11-20T05:00:00Z",
  updated_at: "2026-11-20T05:00:00Z",
};

const makeOnboardingState = (overrides: Partial<OnboardingState> = {}): OnboardingState => ({
  id: "onb-01",
  tenant_id: "tenant-01",
  current_step: 1,
  completed_steps: [],
  business_type: "Software / IT Services",
  structure_config: {},
  is_live: false,
  launched_at: null,
  updated_by: null,
  created_at: "2026-11-20T05:00:00Z",
  updated_at: "2026-11-20T05:00:00Z",
  ...overrides,
});

const makeReadiness = (overrides: Partial<ReadinessChecklist> = {}): ReadinessChecklist => ({
  connectors_ok: true,
  policies_ok: true,
  departments_ok: true,
  employees_ok: true,
  can_go_live: true,
  counts: {
    connectors: 1,
    policies: 1,
    departments: 2,
    employees: 10,
  },
  missing_items: [],
  ...overrides,
});

describe("Stage 4 Prompt 6 — Resumable 5-Step Onboarding Wizard UI", () => {
  const mockSaveStep = vi.fn();
  const mockValidateImport = vi.fn();
  const mockDownloadErrors = vi.fn();
  const mockCommitImport = vi.fn();
  const mockLaunchWorkspace = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();

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

    vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
      data: {
        user_id: "user-1",
        clerk_user_id: "clerk-1",
        email: "owner@acme.internal",
        name: "Acme Owner",
        tenant_id: "tenant-01",
        clerk_org_id: "org_acme_tech_01",
        tenant_name: "Acme Tech Solutions",
        role: "owner",
        is_hr_head: true,
        is_payroll_admin: true,
        permissions: ["all"],
      },
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(connectorsHook, "useConnectors").mockReturnValue({
      data: [BASE_CONNECTOR],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(connectorsHook, "useToggleReadOnly").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(connectorsHook, "useRefreshConnector").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(connectorsHook, "useReconnectConnector").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(policiesHook, "usePolicies").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(policiesHook, "useUploadPolicy").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(policiesHook, "useDeletePolicy").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(departmentsHook, "useDepartments").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(departmentsHook, "useCreateDepartment").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(departmentsHook, "useDeleteDepartment").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(employeeImportHook, "useDownloadImportTemplate").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(employeeImportHook, "useValidateEmployeeImport").mockReturnValue({
      mutateAsync: mockValidateImport,
      isPending: false,
    } as any);
    vi.spyOn(employeeImportHook, "useDownloadImportErrorReport").mockReturnValue({
      mutateAsync: mockDownloadErrors,
      isPending: false,
    } as any);
    vi.spyOn(employeeImportHook, "useCommitEmployeeImport").mockReturnValue({
      mutateAsync: mockCommitImport,
      isPending: false,
    } as any);

    vi.spyOn(onboardingHook, "useOnboardingState").mockReturnValue({
      data: makeOnboardingState({ current_step: 1 }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(onboardingHook, "useSaveOnboardingStep").mockReturnValue({
      mutateAsync: mockSaveStep,
      isPending: false,
    } as any);
    vi.spyOn(onboardingHook, "useOnboardingReadiness").mockReturnValue({
      data: makeReadiness(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);
    vi.spyOn(onboardingHook, "useLaunchWorkspace").mockReturnValue({
      mutateAsync: mockLaunchWorkspace,
      isPending: false,
    } as any);

    vi.spyOn(employeesHook, "useEmployees").mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 100, total_pages: 1 },
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(leaveHook, "useWorkWeek").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(leaveHook, "useUpdateWorkWeek").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useLeaveTypes").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(leaveHook, "useCreateLeaveType").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useUpdateLeaveType").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useDeleteLeaveType").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useHolidays").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(leaveHook, "useCreateHoliday").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useUpdateHoliday").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useDeleteHoliday").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
  });

  it("resumes automatically from saved current_step=3 once without overriding later step clicks", () => {
    vi.spyOn(onboardingHook, "useOnboardingState").mockReturnValue({
      data: makeOnboardingState({
        current_step: 3,
        completed_steps: [1, 2],
        business_type: "Software / IT Services",
      }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    const { rerender } = render(<OnboardingPage />);

    expect(screen.getByText(/Step 3 — Company Structure/i)).toBeInTheDocument();
    expect(screen.getByText(/Stage 6 — Live/i)).toBeInTheDocument();
    expect(screen.getByText("Weekend Schedule")).toBeInTheDocument();
    expect(screen.getByText(/40% Complete/i)).toBeInTheDocument();

    // User clicks back to Step 1
    fireEvent.click(screen.getByRole("button", { name: /Connect Tools/i }));
    expect(screen.getByText(/Step 1 — Google Workspace Connector/i)).toBeInTheDocument();

    // Re-rendering (e.g., query refetch) must NOT snap user back to Step 3
    rerender(<OnboardingPage />);
    expect(screen.getByText(/Step 1 — Google Workspace Connector/i)).toBeInTheDocument();
  });

  it("enforces Excel error report download and disabled commit when invalid_count > 0", async () => {
    vi.spyOn(onboardingHook, "useOnboardingState").mockReturnValue({
      data: makeOnboardingState({ current_step: 4, completed_steps: [1, 2, 3] }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    mockValidateImport.mockResolvedValueOnce({
      import_batch_id: "batch-err-1",
      file_name: "employees_bad.xlsx",
      total_rows: 3,
      valid_count: 1,
      invalid_count: 2,
      can_commit: false,
      preview_rows: [
        {
          row_number: 2,
          full_name: "Ayesha Khan",
          email: "ayesha@acme.internal",
          department: "Engineering",
          job_title: "Engineer",
          employment_type: "full_time",
          join_date: "2026-01-15",
          cnic_masked: "*****-*******-1",
          bank_account_masked: "****1234",
          is_valid: true,
          errors: [],
        },
        {
          row_number: 3,
          full_name: "Bilal Error",
          email: "not-an-email",
          department: "UnknownDept",
          job_title: "Analyst",
          employment_type: "full_time",
          join_date: "2026-01-15",
          cnic_masked: "*****-*******-9",
          bank_account_masked: "****5678",
          is_valid: false,
          errors: ["Invalid email format", "Unknown department"],
        },
      ],
      errors: [
        { row_number: 3, field: "email", message: "Invalid email format" },
        { row_number: 3, field: "department", message: "Unknown department" },
      ],
    });

    render(<OnboardingPage />);

    const fileInput = screen.getByLabelText(/Upload filled employee Excel workbook/i);
    const badFile = new File(["dummy"], "employees_bad.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    fireEvent.change(fileInput, { target: { files: [badFile] } });

    await waitFor(() => {
      expect(screen.getByText("*****-*******-9")).toBeInTheDocument();
      expect(screen.getByText("****5678")).toBeInTheDocument();
    });

    const errReportBtn = screen.getByRole("button", { name: /Download Error Report/i });
    expect(errReportBtn).toBeInTheDocument();

    const commitBtn = screen.getByRole("button", { name: /Confirm & Commit Employees/i });
    expect(commitBtn).toBeDisabled();

    fireEvent.click(errReportBtn);
    await waitFor(() => {
      expect(mockDownloadErrors).toHaveBeenCalledWith("batch-err-1");
    });
  });

  it("enables Confirm & Commit on clean Excel validation and shows committed count", async () => {
    vi.spyOn(onboardingHook, "useOnboardingState").mockReturnValue({
      data: makeOnboardingState({ current_step: 4, completed_steps: [1, 2, 3] }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    mockValidateImport.mockResolvedValueOnce({
      import_batch_id: "batch-clean-1",
      file_name: "employees_clean.xlsx",
      total_rows: 2,
      valid_count: 2,
      invalid_count: 0,
      can_commit: true,
      preview_rows: [
        {
          row_number: 2,
          full_name: "Ali Raza",
          email: "ali.raza@acme.internal",
          department: "Engineering",
          job_title: "Backend Engineer",
          employment_type: "full_time",
          join_date: "2026-01-10",
          cnic_masked: "*****-*******-3",
          bank_account_masked: "****9012",
          is_valid: true,
          errors: [],
        },
      ],
      errors: [],
    });

    mockCommitImport.mockResolvedValueOnce({
      status: "committed",
      import_batch_id: "batch-clean-1",
      committed_count: 2,
      sheets_queued: 2,
      employee_ids: ["emp-1", "emp-2"],
    });

    render(<OnboardingPage />);

    const fileInput = screen.getByLabelText(/Upload filled employee Excel workbook/i);
    const cleanFile = new File(["clean"], "employees_clean.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    fireEvent.change(fileInput, { target: { files: [cleanFile] } });

    await waitFor(() => {
      expect(screen.getByText("*****-*******-3")).toBeInTheDocument();
    });

    expect(
      screen.queryByRole("button", { name: /Download Error Report/i })
    ).not.toBeInTheDocument();

    const commitBtn = screen.getByRole("button", { name: /Confirm & Commit Employees/i });
    expect(commitBtn).not.toBeDisabled();

    fireEvent.click(commitBtn);
    await waitFor(() => {
      expect(mockCommitImport).toHaveBeenCalledWith("batch-clean-1");
      expect(screen.getByText(/Committed 2 employees to PostgreSQL/i)).toBeInTheDocument();
    });
  });

  it("keeps Step 5 Go Live disabled until all 4 checklist items pass and launches when green", async () => {
    vi.spyOn(onboardingHook, "useOnboardingState").mockReturnValue({
      data: makeOnboardingState({ current_step: 5, completed_steps: [1, 2, 3, 4] }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(onboardingHook, "useOnboardingReadiness").mockReturnValue({
      data: makeReadiness({
        policies_ok: false,
        can_go_live: false,
        counts: { connectors: 1, policies: 0, departments: 2, employees: 10 },
        missing_items: ["Upload at least 1 policy"],
      }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    const { rerender } = render(<OnboardingPage />);

    const goLiveBtn = screen.getByRole("button", { name: /Go Live & Send Invitations/i });
    expect(goLiveBtn).toBeDisabled();
    expect(screen.getByText(/Fix in Step 2/i)).toBeInTheDocument();

    // Update readiness to all 4 green
    vi.spyOn(onboardingHook, "useOnboardingReadiness").mockReturnValue({
      data: makeReadiness({ can_go_live: true }),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);
    mockLaunchWorkspace.mockResolvedValueOnce({
      status: "launched",
      is_live: true,
      launched_at: "2026-11-20T10:00:00+05:00",
      invitations_sent: 10,
      invitations_skipped: 0,
      sheets_queued: 10,
      readiness: makeReadiness({ can_go_live: true }),
    });

    rerender(<OnboardingPage />);

    const enabledGoLive = screen.getByRole("button", { name: /Go Live & Send Invitations/i });
    expect(enabledGoLive).not.toBeDisabled();

    fireEvent.click(enabledGoLive);
    await waitFor(() => {
      expect(mockLaunchWorkspace).toHaveBeenCalledWith({
        idempotencyKey: expect.stringMatching(/^onboarding-launch-/),
      });
      expect(screen.getByText(/Workspace is Live/i)).toBeInTheDocument();
      expect(screen.getByText(/10:00 PKT/i)).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Open Employee Directory/i })).toHaveAttribute(
        "href",
        "/employees"
      );
    });
  });

  it("shows already-live banner with formatted PKT timestamp and separates Standby from live HTTP errors", () => {
    expect(resolveRouteTitle("/onboarding").title).toBe("Onboarding Wizard");

    // 1. Offline -> renders StandbyBanner cleanly
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
    const { unmount } = render(<OnboardingPage />);
    expect(screen.getByText(/Onboarding Wizard in Standby Mode/i)).toBeInTheDocument();
    unmount();

    // 2. Live HTTP 403 -> renders explicit role="alert" WITHOUT StandbyBanner
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
    vi.spyOn(onboardingHook, "useOnboardingState").mockReturnValue({
      data: makeOnboardingState({
        current_step: 5,
        completed_steps: [1, 2, 3, 4],
        is_live: true,
        launched_at: "2026-11-20T10:00:00+05:00",
      }),
      isLoading: false,
      error: new ApiHttpError(403, "FORBIDDEN", "Access denied for organization."),
      refetch: vi.fn(),
    } as any);

    render(<OnboardingPage />);
    expect(screen.queryByText(/Onboarding Wizard in Standby Mode/i)).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Access denied for organization.");
    expect(screen.getByText(/Workspace is Live/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open Employee Directory/i })).toHaveAttribute(
      "href",
      "/employees"
    );
  });
});
