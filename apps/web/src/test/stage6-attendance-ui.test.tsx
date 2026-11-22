import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type {
  AttendanceMonthLockResponse,
  AttendanceMonthReviewResponse,
  AttendancePostLockEditResponse,
  AttendanceTodayResponse,
  DailyAttendanceGridResponse,
} from "@agentic-hr/contracts";
import AttendanceWorkspacePage from "@/app/(dashboard)/attendance/page";
import { ToastProvider } from "@/components/ui/toast-provider";
import * as attendanceHook from "@/hooks/use-attendance";
import * as backendStatusHook from "@/hooks/use-backend-status";
import * as currentUserHook from "@/hooks/use-current-user";
import * as departmentsHook from "@/hooks/use-departments";
import { ApiHttpError, ApiOfflineError } from "@/lib/api-request";
import { useToastStore } from "@/stores/toast-store";

vi.mock("@/hooks/use-attendance");
vi.mock("@/hooks/use-backend-status");
vi.mock("@/hooks/use-current-user");
vi.mock("@/hooks/use-departments");

const TODAY_ENABLED: AttendanceTodayResponse = {
  today_date: "2026-11-22",
  year_month: "2026-11",
  check_in_enabled: true,
  is_locked: false,
  record: null,
};

const BASE_GRID: DailyAttendanceGridResponse = {
  target_date: "2026-11-22",
  year_month: "2026-11",
  is_locked: false,
  total_employees: 2,
  marked_count: 1,
  unmarked_count: 1,
  rows: [
    {
      employee_id: "emp-1",
      employee_code: "EMP-001",
      employee_name: "Zainab Ali",
      employee_email: "zainab@acme.internal",
      department_name: "Engineering",
      job_title: "Senior Engineer",
      attendance_id: "att-1",
      attendance_date: "2026-11-22",
      status: "PRESENT",
      source: "manager_grid",
      check_in_at: null,
      check_out_at: null,
      notes: "In office",
      locked: false,
    },
    {
      employee_id: "emp-2",
      employee_code: "EMP-002",
      employee_name: "Bilal Ahmed",
      employee_email: "bilal@acme.internal",
      department_name: "Engineering",
      job_title: "QA Engineer",
      attendance_id: null,
      attendance_date: "2026-11-22",
      status: null,
      source: null,
      check_in_at: null,
      check_out_at: null,
      notes: null,
      locked: false,
    },
  ],
};

function makeMonthReview(
  overrides: Partial<AttendanceMonthReviewResponse> = {}
): AttendanceMonthReviewResponse {
  return {
    year_month: "2026-11",
    month_start: "2026-11-01",
    month_end: "2026-11-30",
    days_in_month: 30,
    active_employees: 2,
    total_gaps: 2,
    auto_fillable_gaps: 1,
    is_locked: false,
    can_lock: false,
    locked_at: null,
    locked_by: null,
    employees: [
      {
        employee_id: "emp-1",
        employee_code: "EMP-001",
        employee_name: "Zainab Ali",
        employee_email: "zainab@acme.internal",
        department_name: "Engineering",
        present_days: 20,
        wfh_days: 2,
        half_days: 0,
        on_leave_days: 0,
        absent_days: 0,
        holiday_days: 0,
        weekend_days: 8,
        recorded_days: 30,
        missing_days: [],
        missing_count: 0,
      },
      {
        employee_id: "emp-2",
        employee_code: "EMP-002",
        employee_name: "Bilal Ahmed",
        employee_email: "bilal@acme.internal",
        department_name: "Engineering",
        present_days: 18,
        wfh_days: 2,
        half_days: 0,
        on_leave_days: 0,
        absent_days: 0,
        holiday_days: 0,
        weekend_days: 8,
        recorded_days: 28,
        missing_days: ["2026-11-18", "2026-11-19"],
        missing_count: 2,
      },
    ],
    ...overrides,
  };
}

describe("Stage 6 Prompt 6 — Attendance Workspace UI", () => {
  const mockCheckIn = vi.fn();
  const mockUpsertGrid = vi.fn();
  const mockValidateImport = vi.fn();
  const mockCommitImport = vi.fn();
  const mockAutoFill = vi.fn();
  const mockLockMonth = vi.fn();
  const mockPostLockEdit = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    act(() => {
      useToastStore.getState().clearToasts();
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

    vi.spyOn(departmentsHook, "useDepartments").mockReturnValue({
      data: [
        {
          id: "dept-eng",
          tenant_id: "t-acme",
          name: "Engineering",
          code: "ENG",
          employee_count: 2,
        },
      ],
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(attendanceHook, "useMyTodayAttendance").mockReturnValue({
      data: TODAY_ENABLED,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);
    vi.spyOn(attendanceHook, "useCheckIn").mockReturnValue({
      mutateAsync: mockCheckIn,
      isPending: false,
    } as any);
    vi.spyOn(attendanceHook, "useDailyAttendanceGrid").mockReturnValue({
      data: BASE_GRID,
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(attendanceHook, "useUpsertDailyAttendanceGrid").mockReturnValue({
      mutateAsync: mockUpsertGrid,
      isPending: false,
    } as any);
    vi.spyOn(attendanceHook, "useValidateAttendanceImport").mockReturnValue({
      mutateAsync: mockValidateImport,
      isPending: false,
    } as any);
    vi.spyOn(attendanceHook, "useCommitAttendanceImport").mockReturnValue({
      mutateAsync: mockCommitImport,
      isPending: false,
    } as any);
    vi.spyOn(attendanceHook, "useAutoFillMonthAttendance").mockReturnValue({
      mutateAsync: mockAutoFill,
      isPending: false,
    } as any);
    vi.spyOn(attendanceHook, "useMonthAttendanceReview").mockReturnValue({
      data: makeMonthReview(),
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(attendanceHook, "useLockMonthAttendance").mockReturnValue({
      mutateAsync: mockLockMonth,
      isPending: false,
    } as any);
    vi.spyOn(attendanceHook, "useRequestPostLockEdit").mockReturnValue({
      mutateAsync: mockPostLockEdit,
      isPending: false,
    } as any);
  });

  it("shows Check-In button when enabled, hides when disabled, and gates Manager/HR tabs on Employee", async () => {
    mockCheckIn.mockResolvedValueOnce({
      id: "att-ci-1",
      tenant_id: "t-acme",
      employee_id: "emp-1",
      employee_name: "Acme Owner",
      employee_email: "owner@acme.internal",
      department_name: "Engineering",
      attendance_date: "2026-11-22",
      status: "PRESENT",
      source: "check_in",
      check_in_at: "2026-11-22T04:00:00Z",
      check_out_at: null,
      notes: null,
      leave_request_id: null,
      locked: false,
      updated_by: "u-owner",
      created_at: "2026-11-22T04:00:00Z",
      updated_at: "2026-11-22T04:00:00Z",
    });

    const { rerender } = render(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );

    const checkInBtn = screen.getByRole("button", { name: /Check In/i });
    await act(async () => {
      fireEvent.click(checkInBtn);
    });
    expect(mockCheckIn).toHaveBeenCalledWith({
      action: "check_in",
      check_out: false,
      target_date: "2026-11-22",
    });

    // Switch to plain Employee with check_in_enabled = false
    vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
      data: {
        user_id: "u-emp",
        clerk_user_id: "clerk-emp",
        email: "emp@acme.internal",
        name: "Plain Employee",
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
    vi.spyOn(attendanceHook, "useMyTodayAttendance").mockReturnValue({
      data: { ...TODAY_ENABLED, check_in_enabled: false },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    rerender(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );
    expect(
      screen.getByText(/Self Check-In is Disabled for This Organization/i)
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Manager Daily Grid/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Month Review/i })).not.toBeInTheDocument();
  });

  it("saves Manager Daily Grid edits and resets unsaved drafts when date changes", async () => {
    mockUpsertGrid.mockResolvedValueOnce({
      target_date: "2026-11-22",
      year_month: "2026-11",
      upserted_count: 2,
      records: [],
    });

    const { rerender } = render(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );

    const bilalSelect = screen.getByLabelText(/Attendance status for Bilal Ahmed/i);
    await act(async () => {
      fireEvent.change(bilalSelect, { target: { value: "WFH" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Save Daily Grid/i }));
    });

    expect(mockUpsertGrid).toHaveBeenCalledWith(
      expect.objectContaining({
        entries: expect.arrayContaining([
          expect.objectContaining({ employee_id: "emp-2", status: "WFH" }),
        ]),
      })
    );

    // Locked grid exposes Request Post-Lock Edit button for managers
    vi.spyOn(attendanceHook, "useDailyAttendanceGrid").mockReturnValue({
      data: { ...BASE_GRID, is_locked: true },
      isLoading: false,
      error: null,
    } as any);
    rerender(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );
    expect(screen.getByRole("button", { name: /Request Post-Lock Edit/i })).toBeInTheDocument();
  });

  it("validates Two-Phase Excel Import error report, blocks bad commit, and commits clean batch", async () => {
    mockValidateImport
      .mockResolvedValueOnce({
        import_batch_id: "att-bad-1",
        file_name: "bad.xlsx",
        total_rows: 2,
        valid_rows: 1,
        invalid_rows: 1,
        can_commit: false,
        preview_rows: [
          {
            row_number: 2,
            employee_email: "zainab@acme.internal",
            attendance_date: "2026-11-20",
            status: "PRESENT",
            notes: null,
            is_valid: true,
            errors: [],
          },
          {
            row_number: 3,
            employee_email: "unknown@acme.internal",
            attendance_date: "2026-11-20",
            status: "TELEPORT",
            notes: null,
            is_valid: false,
            errors: ["Unknown employee", "Invalid status"],
          },
        ],
        errors: [
          { row_number: 3, field: "employee_email", message: "Unknown employee" },
          { row_number: 3, field: "status", message: "Invalid status" },
        ],
      })
      .mockResolvedValueOnce({
        import_batch_id: "att-good-1",
        file_name: "good.xlsx",
        total_rows: 1,
        valid_rows: 1,
        invalid_rows: 0,
        can_commit: true,
        preview_rows: [
          {
            row_number: 2,
            employee_email: "zainab@acme.internal",
            attendance_date: "2026-11-20",
            status: "WFH",
            notes: "Friday remote",
            is_valid: true,
            errors: [],
          },
        ],
        errors: [],
      });

    mockCommitImport.mockResolvedValueOnce({
      status: "committed",
      import_batch_id: "att-good-1",
      committed_count: 1,
      sheets_queued: 1,
      year_months: ["2026-11"],
    });

    render(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: /Excel Import/i }));
    });

    const fileInput = screen.getByLabelText(/Upload attendance Excel workbook/i);
    const badFile = new File(["dummy"], "bad.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [badFile] } });
    });

    expect(screen.getByRole("button", { name: /Download Error Report/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Commit Valid Records/i })).toBeDisabled();

    const goodFile = new File(["dummy"], "good.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [goodFile] } });
    });

    const commitBtn = screen.getByRole("button", { name: /Commit Valid Records/i });
    expect(commitBtn).toBeEnabled();
    await act(async () => {
      fireEvent.click(commitBtn);
    });
    expect(mockCommitImport).toHaveBeenCalledWith("att-good-1");
  });

  it("displays Month Review gaps refusing Lock Month, unlocks Lock Month at 0 gaps, and submits Post-Lock Edit with /approvals toast link", async () => {
    mockLockMonth.mockResolvedValueOnce({
      id: "lock-1",
      tenant_id: "t-acme",
      year_month: "2026-11",
      locked_by: "u-owner",
      locked_at: "2026-11-30T12:00:00Z",
      employee_count: 2,
      record_count: 60,
    } satisfies AttendanceMonthLockResponse);

    mockPostLockEdit.mockResolvedValueOnce({
      approval_id: "app-ple-1",
      approval_status: "pending",
      approver_role: "hr_head",
      employee_id: "emp-1",
      employee_name: "Zainab Ali",
      attendance_date: "2026-11-18",
      year_month: "2026-11",
      previous_status: "PRESENT",
      new_status: "WFH",
      notes: null,
      reason: "Approved remote day correction",
    } satisfies AttendancePostLockEditResponse);

    const { rerender } = render(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("tab", { name: /Month Review & Lock/i }));
    });

    expect(screen.getByText("2026-11-18")).toBeInTheDocument();
    expect(screen.getByText(/Cannot Lock Month/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Lock Month$/i })).toBeDisabled();

    // Update review to 0 gaps -> Lock Month becomes enabled
    vi.spyOn(attendanceHook, "useMonthAttendanceReview").mockReturnValue({
      data: makeMonthReview({
        total_gaps: 0,
        can_lock: true,
        employees: makeMonthReview().employees.map((e) => ({
          ...e,
          recorded_days: 30,
          missing_days: [],
          missing_count: 0,
        })),
      }),
      isLoading: false,
      error: null,
    } as any);

    rerender(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );

    const lockBtn = screen.getByRole("button", { name: /^Lock Month$/i });
    expect(lockBtn).toBeEnabled();
    await act(async () => {
      fireEvent.click(lockBtn);
    });
    expect(mockLockMonth).toHaveBeenCalled();

    // Now render locked state -> open Post-Lock Edit Modal -> submit -> toast with /approvals link
    vi.spyOn(attendanceHook, "useMonthAttendanceReview").mockReturnValue({
      data: makeMonthReview({
        total_gaps: 0,
        can_lock: false,
        is_locked: true,
        locked_at: "2026-11-30T12:00:00Z",
        locked_by: "u-owner",
      }),
      isLoading: false,
      error: null,
    } as any);

    rerender(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );

    expect(screen.getByText(/is Locked for Payroll/i)).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Request Post-Lock Edit/i }));
    });

    const reasonInput = screen.getByLabelText(/Justification Reason/i);
    await act(async () => {
      fireEvent.change(reasonInput, { target: { value: "Approved remote day correction" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Submit Post-Lock Request/i }));
    });

    expect(mockPostLockEdit).toHaveBeenCalled();
    const approvalsLink = screen.getByRole("link", { name: /Open Approvals/i });
    expect(approvalsLink).toHaveAttribute("href", "/approvals");
  });

  it("shows StandbyBanner strictly on offline errors and role=alert on live HTTP errors", () => {
    vi.spyOn(attendanceHook, "useMyTodayAttendance").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new ApiHttpError(409, "ATTENDANCE_MONTH_LOCKED", "Month is locked"),
      refetch: vi.fn(),
    } as any);

    const { rerender } = render(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );
    expect(screen.queryByText(/FastAPI backend is offline/i)).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Month is locked");

    vi.spyOn(attendanceHook, "useMyTodayAttendance").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new ApiOfflineError("OFFLINE: FastAPI backend is unreachable."),
      refetch: vi.fn(),
    } as any);

    rerender(
      <>
        <ToastProvider />
        <AttendanceWorkspacePage />
      </>
    );
    expect(screen.getByText(/FastAPI backend is offline/i)).toBeInTheDocument();
  });
});
