import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type {
  HolidayResponse,
  LeaveBalanceItemResponse,
  LeaveOverviewResponse,
  LeaveRequestResponse,
  LeaveTypeResponse,
  WorkWeekResponse,
} from "@agentic-hr/contracts";
import LeaveWorkspacePage from "@/app/(dashboard)/leave/page";
import SettingsPage from "@/app/(dashboard)/settings/page";
import { resolveRouteTitle } from "@/components/layout/header";
import { Step3CompanyStructure } from "@/components/onboarding/step3-company-structure";
import { ToastProvider } from "@/components/ui/toast-provider";
import * as backendStatusHook from "@/hooks/use-backend-status";
import * as connectorsHook from "@/hooks/use-connectors";
import * as currentUserHook from "@/hooks/use-current-user";
import * as departmentsHook from "@/hooks/use-departments";
import * as employeesHook from "@/hooks/use-employees";
import * as leaveHook from "@/hooks/use-leave";
import { ApiHttpError, ApiOfflineError } from "@/lib/api-request";
import { useConnectorStore } from "@/stores/connector-store";
import { useToastStore } from "@/stores/toast-store";

vi.mock("@/hooks/use-backend-status");
vi.mock("@/hooks/use-connectors");
vi.mock("@/hooks/use-current-user");
vi.mock("@/hooks/use-departments");
vi.mock("@/hooks/use-employees");
vi.mock("@/hooks/use-leave");

const BASE_WORK_WEEK: WorkWeekResponse = {
  id: "ww-1",
  tenant_id: "t-acme",
  weekend_days: ["Saturday", "Sunday"],
  probation_months: 3,
  max_team_absent_pct: "30.00",
  min_team_present: 1,
  backup_approver_user_id: null,
  check_in_enabled: true,
  timezone: "Asia/Karachi",
  created_at: "2026-11-20T09:00:00Z",
  updated_at: "2026-11-20T09:00:00Z",
};

const BASE_LEAVE_TYPES: LeaveTypeResponse[] = [
  {
    id: "lt-annual",
    tenant_id: "t-acme",
    code: "annual",
    name: "Annual Leave",
    annual_allowance_days: "20.00",
    accrual_mode: "upfront",
    carry_forward_cap_days: "5.00",
    max_consecutive_days: 15,
    min_notice_days: 3,
    allow_during_probation: false,
    requires_document_after_days: null,
    allow_half_day: true,
    is_paid: true,
    is_active: true,
    created_at: "2026-11-20T09:00:00Z",
    updated_at: "2026-11-20T09:00:00Z",
  },
];

const BASE_HOLIDAYS: HolidayResponse[] = [
  {
    id: "hol-1",
    tenant_id: "t-acme",
    holiday_date: "2026-08-14",
    name: "Independence Day",
    is_optional: false,
    source: "default_pk",
    created_at: "2026-11-20T09:00:00Z",
    updated_at: "2026-11-20T09:00:00Z",
  },
];

const BASE_BALANCES: LeaveBalanceItemResponse[] = [
  {
    id: "bal-1",
    tenant_id: "t-acme",
    employee_id: "emp-1",
    employee_name: "Ali Raza",
    leave_type_id: "lt-annual",
    leave_type_code: "annual",
    leave_type_name: "Annual Leave",
    year: 2026,
    allocated_days: "20.00",
    carried_forward_days: "2.00",
    reserved_days: "1.50",
    used_days: "3.00",
    available_days: "17.50",
    is_paid: true,
    accrual_mode: "upfront",
  },
];

function makeLeaveRequest(overrides: Partial<LeaveRequestResponse> = {}): LeaveRequestResponse {
  return {
    id: "lr-1",
    tenant_id: "t-acme",
    employee_id: "emp-1",
    employee_name: "Ali Raza",
    employee_email: "ali@acme.internal",
    department_name: "Engineering",
    leave_type_id: "lt-annual",
    leave_type_code: "annual",
    leave_type_name: "Annual Leave",
    start_date: "2026-12-07",
    end_date: "2026-12-08",
    is_half_day: false,
    half_day_period: null,
    working_days: "2.00",
    reason: "Family trip",
    document_id: null,
    policy_check_status: "OK",
    policy_check_reasons: [],
    status: "pending",
    approver_user_id: "u-mgr",
    escalated_to_user_id: null,
    hr_exception_approval_id: null,
    hr_exception_status: null,
    workflow_id: null,
    calendar_event_id: null,
    sheets_row_synced: false,
    decided_by: null,
    decided_at: null,
    decision_note: null,
    created_at: "2026-11-22T09:00:00Z",
    updated_at: "2026-11-22T09:00:00Z",
    ...overrides,
  };
}

describe("Stage 6 Prompt 5 — Leave Workspace, AI Intake, Settings & Step 3 UI", () => {
  const mockIntake = vi.fn();
  const mockSubmit = vi.fn();
  const mockDecide = vi.fn();
  const mockCancel = vi.fn();
  const mockUpdateWorkWeek = vi.fn();
  const mockCreateLeaveType = vi.fn();
  const mockUpdateLeaveType = vi.fn();
  const mockDeleteLeaveType = vi.fn();
  const mockCreateHoliday = vi.fn();
  const mockUpdateHoliday = vi.fn();
  const mockDeleteHoliday = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    act(() => {
      useToastStore.getState().clearToasts();
      useConnectorStore.setState({ bannerMessage: null, errorMessage: null });
    });

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
        user_id: "u-owner",
        clerk_user_id: "clerk-owner",
        email: "owner@acme.internal",
        name: "Acme Owner",
        tenant_id: "t-acme",
        clerk_org_id: "org_acme",
        tenant_name: "Acme Tech Solutions",
        role: "owner",
        is_hr_head: true,
        is_payroll_admin: true,
        permissions: ["owner"],
      },
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(connectorsHook, "useConnectors").mockReturnValue({
      data: [],
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
    vi.spyOn(connectorsHook, "useRevokeConnector").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(connectorsHook, "useReconnectConnector").mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(connectorsHook, "useGoogleAuthorize").mockReturnValue({
      mutate: vi.fn(),
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

    vi.spyOn(employeesHook, "useEmployees").mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 100, total_pages: 1 },
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(leaveHook, "useWorkWeek").mockReturnValue({
      data: BASE_WORK_WEEK,
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(leaveHook, "useUpdateWorkWeek").mockReturnValue({
      mutateAsync: mockUpdateWorkWeek,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useLeaveTypes").mockReturnValue({
      data: BASE_LEAVE_TYPES,
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(leaveHook, "useCreateLeaveType").mockReturnValue({
      mutateAsync: mockCreateLeaveType,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useUpdateLeaveType").mockReturnValue({
      mutateAsync: mockUpdateLeaveType,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useDeleteLeaveType").mockReturnValue({
      mutateAsync: mockDeleteLeaveType,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useHolidays").mockReturnValue({
      data: BASE_HOLIDAYS,
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(leaveHook, "useCreateHoliday").mockReturnValue({
      mutateAsync: mockCreateHoliday,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useUpdateHoliday").mockReturnValue({
      mutateAsync: mockUpdateHoliday,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useDeleteHoliday").mockReturnValue({
      mutateAsync: mockDeleteHoliday,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useMyLeaveBalances").mockReturnValue({
      data: BASE_BALANCES,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(leaveHook, "useMyLeaveRequests").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(leaveHook, "useLeaveInbox").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(leaveHook, "useTeamLeaveCalendar").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(leaveHook, "useLeaveOverview").mockReturnValue({
      data: {
        total_requests: 0,
        pending_count: 0,
        pending_hr_exception_count: 0,
        approved_count: 0,
        rejected_count: 0,
        cancelled_count: 0,
        expired_count: 0,
        requests: [],
      } satisfies LeaveOverviewResponse,
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(leaveHook, "useLeaveIntake").mockReturnValue({
      mutateAsync: mockIntake,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useSubmitLeaveRequest").mockReturnValue({
      mutateAsync: mockSubmit,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useDecideLeaveRequest").mockReturnValue({
      mutateAsync: mockDecide,
      isPending: false,
    } as any);
    vi.spyOn(leaveHook, "useCancelLeaveRequest").mockReturnValue({
      mutateAsync: mockCancel,
      isPending: false,
    } as any);
  });

  it("renders balance cards from Decimal strings, route titles, and attendance placeholder safely", () => {
    expect(resolveRouteTitle("/leave").title).toBe("Leave Management");
    expect(resolveRouteTitle("/attendance").title).toBe("Attendance & Month Lock");

    render(
      <>
        <ToastProvider />
        <LeaveWorkspacePage />
      </>
    );

    expect(screen.getByText("Annual Leave")).toBeInTheDocument();
    expect(screen.getByText("17.5")).toBeInTheDocument();
    expect(screen.getByText("1.5")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
  });

  it("drives AI Leave Assistant clarifying question -> Confirm Card -> submit", async () => {
    mockIntake
      .mockResolvedValueOnce({
        status: "needs_clarification",
        clarifying_question: "Which leave type would you like to apply for (annual, sick, casual)?",
        extracted: { start_date: "2026-12-07", end_date: "2026-12-08" },
        confirm_card: null,
      })
      .mockResolvedValueOnce({
        status: "ready_to_confirm",
        clarifying_question: null,
        extracted: { leave_type_code: "annual", start_date: "2026-12-07", end_date: "2026-12-08" },
        confirm_card: {
          employee_id: "emp-1",
          employee_name: "Ali Raza",
          leave_type_id: "lt-annual",
          leave_type_code: "annual",
          leave_type_name: "Annual Leave",
          start_date: "2026-12-07",
          end_date: "2026-12-08",
          is_half_day: false,
          half_day_period: null,
          working_days: "2.00",
          reason: "Family trip",
          document_id: null,
          balance_before: "17.50",
          balance_after: "15.50",
          policy_check_status: "OK",
          policy_check_reasons: [
            {
              rule: "notice",
              status: "OK",
              code: "NOTICE_OK",
              message: "Notice period satisfied.",
            },
          ],
        },
      });
    mockSubmit.mockResolvedValueOnce(makeLeaveRequest({ id: "lr-ai-1", status: "pending" }));

    render(
      <>
        <ToastProvider />
        <LeaveWorkspacePage />
      </>
    );

    const textarea = screen.getByLabelText(/Leave Request Message/i);
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "2 din ki chutti 2026-12-07 se" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Analyze Leave Request/i }));
    });

    await waitFor(() => {
      expect(screen.getByText(/Which leave type would you like to apply for/i)).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.change(textarea, { target: { value: "Annual leave" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Analyze Leave Request/i }));
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Confirm & Submit Leave Request/i })).toBeEnabled();
      expect(screen.getByText(/15.5/)).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Confirm & Submit Leave Request/i }));
    });

    expect(mockSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        leave_type_code: "annual",
        start_date: "2026-12-07",
        end_date: "2026-12-08",
      })
    );
  });

  it("submits Plain Form without empty document_id and supports Cancel Approved Leave", async () => {
    vi.spyOn(leaveHook, "useMyLeaveRequests").mockReturnValue({
      data: [makeLeaveRequest({ id: "lr-app-1", status: "approved" })],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);
    mockSubmit.mockResolvedValueOnce(makeLeaveRequest({ id: "lr-form-1" }));
    mockCancel.mockResolvedValueOnce(makeLeaveRequest({ id: "lr-app-1", status: "cancelled" }));

    render(
      <>
        <ToastProvider />
        <LeaveWorkspacePage />
      </>
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Plain Leave Form/i }));
    });

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/Start Date/i), { target: { value: "2026-12-10" } });
      fireEvent.change(screen.getByLabelText(/End Date/i), { target: { value: "2026-12-11" } });
      fireEvent.change(screen.getByLabelText(/^Reason$/i), {
        target: { value: "Medical checkup" },
      });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Submit Leave Request/i }));
    });

    expect(mockSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        leave_type_code: "annual",
        start_date: "2026-12-10",
        end_date: "2026-12-11",
        reason: "Medical checkup",
        document_id: undefined,
      })
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: /My Requests/i }));
    });
    const cancelBtn = screen.getByRole("button", { name: /Cancel Approved Leave/i });
    await act(async () => {
      fireEvent.click(cancelBtn);
    });
    expect(mockCancel).toHaveBeenCalledWith({
      requestId: "lr-app-1",
      payload: { reason: "Cancelled by employee" },
    });
  });

  it("renders Manager Inbox VIOLATION highlights, approve/reject decision, and HR Overview approvals link", async () => {
    const violationReq = makeLeaveRequest({
      id: "lr-viol-1",
      policy_check_status: "VIOLATION",
      policy_check_reasons: [
        {
          rule: "probation",
          status: "VIOLATION",
          code: "PROBATION_RESTRICTION",
          message: "Employee is within 3-month probation.",
        },
      ],
    });
    vi.spyOn(leaveHook, "useLeaveInbox").mockReturnValue({
      data: [violationReq],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(leaveHook, "useLeaveOverview").mockReturnValue({
      data: {
        total_requests: 1,
        pending_count: 0,
        pending_hr_exception_count: 1,
        approved_count: 0,
        rejected_count: 0,
        cancelled_count: 0,
        expired_count: 0,
        requests: [
          {
            ...violationReq,
            hr_exception_approval_id: "app-exc-1",
            status: "pending_hr_exception",
          },
        ],
      },
      isLoading: false,
      error: null,
    } as any);
    mockDecide.mockResolvedValueOnce({ ...violationReq, status: "pending_hr_exception" });

    render(
      <>
        <ToastProvider />
        <LeaveWorkspacePage />
      </>
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: /Manager Inbox/i }));
    });
    expect(screen.getByText(/PROBATION_RESTRICTION/i)).toBeInTheDocument();

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/Decision note for Ali Raza/i), {
        target: { value: "Approved subject to HR" },
      });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^Approve$/i }));
    });
    expect(mockDecide).toHaveBeenCalledWith({
      requestId: "lr-viol-1",
      payload: { decision: "approved", note: "Approved subject to HR" },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: /HR Overview/i }));
    });
    const appLink = screen.getByRole("link", { name: /Open Approvals Inbox/i });
    expect(appLink).toHaveAttribute("href", "/approvals");
  });

  it("gates Manager/HR tabs & backup approver fetch on role and configures Settings & Step 3", async () => {
    vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
      data: {
        user_id: "u-emp",
        clerk_user_id: "clerk-emp",
        email: "emp@acme.internal",
        name: "Regular Employee",
        tenant_id: "t-acme",
        clerk_org_id: "org_acme",
        tenant_name: "Acme Tech Solutions",
        role: "employee",
        is_hr_head: false,
        is_payroll_admin: false,
        permissions: [],
      },
      isLoading: false,
      error: null,
    } as any);

    render(
      <>
        <ToastProvider />
        <LeaveWorkspacePage />
      </>
    );
    expect(leaveHook.useLeaveInbox).toHaveBeenCalledWith(false);
    expect(screen.queryByRole("tab", { name: /Manager Inbox/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /HR Overview/i })).not.toBeInTheDocument();

    // Now test Owner in Settings Leave & Attendance Rules + Step 3 Onboarding
    vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
      data: {
        user_id: "u-owner",
        clerk_user_id: "clerk-owner",
        email: "owner@acme.internal",
        name: "Acme Owner",
        tenant_id: "t-acme",
        clerk_org_id: "org_acme",
        tenant_name: "Acme Tech Solutions",
        role: "owner",
        is_hr_head: true,
        is_payroll_admin: true,
        permissions: ["owner"],
      },
      isLoading: false,
      error: null,
    } as any);
    mockUpdateWorkWeek.mockResolvedValueOnce(BASE_WORK_WEEK);

    render(
      <>
        <ToastProvider />
        <SettingsPage />
      </>
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: /Leave & Attendance Rules/i }));
    });
    expect(screen.getByText(/Work-Week, Probation & Attendance Rules/i)).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Save Work-Week Rules/i }));
    });
    expect(mockUpdateWorkWeek).toHaveBeenCalled();

    const mockSaveStep3 = vi.fn();
    render(
      <>
        <ToastProvider />
        <Step3CompanyStructure
          isSavingStep={false}
          onBack={vi.fn()}
          onSaveAndContinue={mockSaveStep3}
        />
      </>
    );
    expect(screen.getByText("Stage 6 — Live")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Save & Continue/i }));
    });
    expect(mockSaveStep3).toHaveBeenCalledWith(
      expect.objectContaining({
        advance_step: true,
        structure_config: expect.objectContaining({
          weekends: ["Saturday", "Sunday"],
          holidays: [{ date: "2026-08-14", name: "Independence Day" }],
        }),
      })
    );
  });

  it("shows StandbyBanner strictly on offline errors and role=alert on live HTTP errors", () => {
    vi.spyOn(leaveHook, "useMyLeaveBalances").mockReturnValue({
      data: [],
      isLoading: false,
      error: new ApiHttpError(400, "VALIDATION_ERROR", "Invalid year requested"),
      refetch: vi.fn(),
    } as any);

    const { rerender } = render(
      <>
        <ToastProvider />
        <LeaveWorkspacePage />
      </>
    );
    expect(screen.queryByText(/FastAPI backend is offline/i)).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Invalid year requested");

    vi.spyOn(leaveHook, "useMyLeaveBalances").mockReturnValue({
      data: [],
      isLoading: false,
      error: new ApiOfflineError("OFFLINE: FastAPI backend is unreachable."),
      refetch: vi.fn(),
    } as any);

    rerender(
      <>
        <ToastProvider />
        <LeaveWorkspacePage />
      </>
    );
    expect(screen.getByText(/FastAPI backend is offline/i)).toBeInTheDocument();
  });
});
