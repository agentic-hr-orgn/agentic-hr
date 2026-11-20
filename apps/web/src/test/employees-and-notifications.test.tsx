import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import EmployeesPage from "@/app/(dashboard)/employees/page";
import { Header } from "@/components/layout/header";
import * as backendStatusHook from "@/hooks/use-backend-status";
import * as currentUserHook from "@/hooks/use-current-user";
import * as departmentsHook from "@/hooks/use-departments";
import * as employeesHook from "@/hooks/use-employees";
import * as notificationsHook from "@/hooks/use-notifications";
import * as uiStoreHook from "@/stores/ui-store";
import { ApiHttpError, ApiOfflineError } from "@/lib/api-request";
import { getTodayPKTDateString } from "@/lib/format";
import type {
  EmployeeListResponse,
  EmployeeProfile,
  OrgChartResponse,
} from "@agentic-hr/contracts";

vi.mock("@clerk/nextjs", () => ({
  useAuth: () => ({
    isLoaded: true,
    isSignedIn: false,
    orgId: "org_acme_tech_01",
    getToken: vi.fn(),
  }),
  OrganizationSwitcher: () => <div data-testid="org-switcher" />,
  UserButton: () => <div data-testid="user-button" />,
}));
vi.mock("@/hooks/use-backend-status");
vi.mock("@/hooks/use-current-user");
vi.mock("@/hooks/use-departments");
vi.mock("@/hooks/use-employees");
vi.mock("@/hooks/use-notifications");
vi.mock("@/stores/ui-store");

function addDaysToIsoDate(baseIso: string, deltaDays: number): string {
  const [y, m, d] = baseIso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + deltaDays));
  return dt.toISOString().slice(0, 10);
}

const SAMPLE_LIST: EmployeeListResponse = {
  items: [
    {
      id: "emp-1",
      tenant_id: "tenant-1",
      employee_code: "EMP-001",
      full_name: "Ubaid Qureshi",
      email: "ubaid@acme.internal",
      department_id: "dept-1",
      department_name: "Engineering",
      manager_id: null,
      manager_name: null,
      job_title: "CTO",
      employment_type: "full_time",
      join_date: "2024-01-01",
      status: "active",
      created_at: "2026-11-20T05:00:00+05:00",
      updated_at: "2026-11-20T05:00:00+05:00",
    },
  ],
  total: 20,
  page: 1,
  page_size: 15,
  total_pages: 2,
};

const SENSITIVE_PROFILE: EmployeeProfile = {
  ...SAMPLE_LIST.items[0],
  manager_email: null,
  can_view_sensitive: true,
  can_edit_sensitive: true,
  cnic: "42101-1234567-1",
  bank_account: "PK36ACME000100001234",
  cnic_masked: "*****-*******-1",
  bank_account_masked: "****1234",
};

const RESTRICTED_PROFILE: EmployeeProfile = {
  ...SAMPLE_LIST.items[0],
  manager_email: null,
  can_view_sensitive: false,
  can_edit_sensitive: false,
  cnic: null,
  bank_account: null,
  cnic_masked: null,
  bank_account_masked: null,
};

describe("Stage 4 Prompt 7 — Live Directory, PII Drawer, Org Chart & Notification Bell", () => {
  const mockUpdateEmployee = vi.fn();
  const mockUploadDoc = vi.fn();
  const mockDownloadDoc = vi.fn();
  const mockDeleteDoc = vi.fn();
  const mockMarkRead = vi.fn();
  const mockMarkAllRead = vi.fn();
  const mockTriggerScan = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();

    vi.spyOn(uiStoreHook, "useUIStore").mockImplementation((selector: any) =>
      selector({ toggleSidebar: vi.fn(), isSidebarOpen: false, setSidebarOpen: vi.fn() })
    );

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
        tenant_id: "tenant-1",
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

    vi.spyOn(departmentsHook, "useDepartments").mockReturnValue({
      data: [
        {
          id: "dept-1",
          tenant_id: "tenant-1",
          name: "Engineering",
          code: "ENG",
          employee_count: 10,
          created_at: "2026-11-20T05:00:00+05:00",
          updated_at: "2026-11-20T05:00:00+05:00",
        },
      ],
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(employeesHook, "useEmployees").mockReturnValue({
      data: SAMPLE_LIST,
      isLoading: false,
      isFetching: false,
      dataUpdatedAt: Date.now(),
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.spyOn(employeesHook, "useCreateEmployee").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    vi.spyOn(employeesHook, "useEmployeeProfile").mockReturnValue({
      data: SENSITIVE_PROFILE,
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(employeesHook, "useUpdateEmployee").mockReturnValue({
      mutateAsync: mockUpdateEmployee,
      isPending: false,
    } as any);

    const todayPkt = getTodayPKTDateString();
    vi.spyOn(employeesHook, "useEmployeeDocuments").mockReturnValue({
      data: [
        {
          id: "doc-1",
          tenant_id: "tenant-1",
          employee_id: "emp-1",
          document_type: "contract",
          title: "Employment Contract",
          file_name: "contract.pdf",
          mime_type: "application/pdf",
          size_bytes: 2048,
          storage_provider: "google_drive",
          storage_key: "drive-file-1",
          expiry_date: addDaysToIsoDate(todayPkt, 5),
          created_at: "2026-11-20T05:00:00+05:00",
          updated_at: "2026-11-20T05:00:00+05:00",
        },
        {
          id: "doc-2",
          tenant_id: "tenant-1",
          employee_id: "emp-1",
          document_type: "certificate",
          title: "AWS Certificate",
          file_name: "aws.pdf",
          mime_type: "application/pdf",
          size_bytes: 4096,
          storage_provider: "local",
          storage_key: "tenant-1/aws.pdf",
          expiry_date: addDaysToIsoDate(todayPkt, 20),
          created_at: "2026-11-20T05:00:00+05:00",
          updated_at: "2026-11-20T05:00:00+05:00",
        },
      ],
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(employeesHook, "useUploadEmployeeDocument").mockReturnValue({
      mutateAsync: mockUploadDoc,
      isPending: false,
    } as any);
    vi.spyOn(employeesHook, "useDownloadEmployeeDocument").mockReturnValue({
      mutateAsync: mockDownloadDoc,
      isPending: false,
    } as any);
    vi.spyOn(employeesHook, "useDeleteEmployeeDocument").mockReturnValue({
      mutateAsync: mockDeleteDoc,
      isPending: false,
    } as any);

    const orgData: OrgChartResponse = {
      total_employees: 3,
      roots: [
        {
          id: "emp-1",
          employee_code: "EMP-001",
          full_name: "Ubaid Qureshi",
          job_title: "CTO",
          department_name: "Engineering",
          status: "active",
          email: "ubaid@acme.internal",
          manager_id: null,
          direct_reports_count: 1,
          children: [
            {
              id: "emp-2",
              employee_code: "EMP-002",
              full_name: "Zainab Ali",
              job_title: "Staff Engineer",
              department_name: "Engineering",
              status: "active",
              email: "zainab@acme.internal",
              manager_id: "emp-1",
              direct_reports_count: 0,
              children: [],
            },
          ],
        },
      ],
      unassigned: [
        {
          id: "emp-3",
          employee_code: "EMP-003",
          full_name: "Orphan Contractor",
          job_title: "Advisor",
          department_name: "Operations",
          status: "active",
          email: "orphan@acme.internal",
          manager_id: "missing-id",
          direct_reports_count: 0,
          children: [],
        },
      ],
    };
    vi.spyOn(employeesHook, "useOrgChart").mockReturnValue({
      data: orgData,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    vi.spyOn(notificationsHook, "useNotifications").mockReturnValue({
      data: {
        items: [
          {
            id: "notif-1",
            tenant_id: "tenant-1",
            recipient_user_id: "user-1",
            recipient_email: "owner@acme.internal",
            category: "document_expiry",
            severity: "critical",
            title: "Security Clearance expires in 1 day",
            message: "Tariq Mahmood's certificate expires tomorrow.",
            entity_type: "employee_document",
            entity_id: "doc-1",
            is_read: false,
            read_at: null,
            created_at: "2026-11-20T10:00:00+05:00",
          },
        ],
        total: 1,
        unread_count: 1,
      },
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(notificationsHook, "useMarkNotificationRead").mockReturnValue({
      mutateAsync: mockMarkRead,
      isPending: false,
    } as any);
    vi.spyOn(notificationsHook, "useMarkAllNotificationsRead").mockReturnValue({
      mutateAsync: mockMarkAllRead.mockResolvedValue({ updated: 1 }),
      isPending: false,
    } as any);
    vi.spyOn(notificationsHook, "useTriggerExpiryScan").mockReturnValue({
      mutateAsync: mockTriggerScan.mockResolvedValue({
        tenant_id: "tenant-1",
        reference_date: "2026-11-21",
        documents_matched: 1,
        alerts_created: 1,
        alerts_deduplicated: 0,
        emails_sent: 1,
        emails_skipped: 0,
      }),
      isPending: false,
    } as any);
  });

  it("renders Directory table, handles search/pagination & resets page to 1 on filter change, and separates offline Standby from HTTP errors", () => {
    const useEmpSpy = vi.spyOn(employeesHook, "useEmployees");
    render(<EmployeesPage />);

    expect(screen.getByText("Ubaid Qureshi")).toBeInTheDocument();
    expect(screen.getByText("ubaid@acme.internal")).toBeInTheDocument();
    expect(screen.getByText(/Page/i)).toHaveTextContent("Page 1 of 2 • 20 total employees");

    // Advance to page 2
    fireEvent.click(screen.getByRole("button", { name: /Next/i }));
    expect(useEmpSpy).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));

    // Changing search resets page back to 1
    const searchInput = screen.getByLabelText(/Search Directory/i);
    fireEvent.change(searchInput, { target: { value: "Zainab" } });
    expect(useEmpSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: "Zainab", page: 1 })
    );

    // Offline -> StandbyBanner shown
    useEmpSpy.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      dataUpdatedAt: 0,
      error: new ApiOfflineError(),
      refetch: vi.fn(),
    } as any);
    const { rerender } = render(<EmployeesPage />);
    expect(screen.getByText(/Directory in Standby Mode/i)).toBeInTheDocument();

    // Live HTTP 403 -> explicit role="alert", NOT StandbyBanner
    useEmpSpy.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      dataUpdatedAt: 0,
      error: new ApiHttpError(403, "FORBIDDEN", "Organization access denied."),
      refetch: vi.fn(),
    } as any);
    rerender(<EmployeesPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("Organization access denied.");
  });

  it("opens Profile Drawer: masked PII by default with Reveal toggle when authorized, and excludes untouched PII on non-PII edit", async () => {
    mockUpdateEmployee.mockResolvedValueOnce(SENSITIVE_PROFILE);
    render(<EmployeesPage />);

    fireEvent.click(screen.getByRole("button", { name: "Ubaid Qureshi" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");

    // Masked by default
    expect(screen.getByTestId("pii-cnic-value")).toHaveTextContent("*****-*******-1");
    expect(screen.getByTestId("pii-bank-value")).toHaveTextContent("****1234");
    expect(screen.queryByText("42101-1234567-1")).not.toBeInTheDocument();

    // Click Reveal -> plaintext revealed
    const revealBtn = screen.getByRole("button", { name: /Reveal/i });
    expect(revealBtn).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(revealBtn);
    expect(screen.getByTestId("pii-cnic-value")).toHaveTextContent("42101-1234567-1");
    expect(screen.getByTestId("pii-bank-value")).toHaveTextContent("PK36ACME000100001234");
    expect(screen.getByRole("button", { name: /Hide/i })).toHaveAttribute("aria-pressed", "true");

    // Edit job_title without touching CNIC or Bank -> PATCH payload omits cnic & bank_account
    fireEvent.click(screen.getByRole("button", { name: /Edit Profile/i }));
    const jobInput = screen.getByLabelText("Job Title");
    fireEvent.change(jobInput, { target: { value: "Chief Architect" } });
    fireEvent.click(screen.getByRole("button", { name: /Save Changes/i }));

    await waitFor(() => {
      expect(mockUpdateEmployee).toHaveBeenCalledTimes(1);
    });
    const callArg = mockUpdateEmployee.mock.calls[0][0];
    expect(callArg.payload.job_title).toBe("Chief Architect");
    expect("cnic" in callArg.payload).toBe(false);
    expect("bank_account" in callArg.payload).toBe(false);
  });

  it("hides Reveal button and displays Restricted lock notice when can_view_sensitive is false", async () => {
    vi.spyOn(employeesHook, "useEmployeeProfile").mockReturnValue({
      data: RESTRICTED_PROFILE,
      isLoading: false,
      error: null,
    } as any);

    render(<EmployeesPage />);
    fireEvent.click(screen.getByRole("button", { name: "Ubaid Qureshi" }));

    expect(
      await screen.findByText(/Restricted — Owner, HR Head, Payroll Admin, or Self Only/i)
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reveal/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId("pii-cnic-value")).not.toBeInTheDocument();
  });

  it("renders Document Vault tab with expiry & storage badges and invokes upload, download, and delete", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockUploadDoc.mockResolvedValueOnce({
      id: "doc-3",
      title: "CNIC Scan",
      storage_provider: "local",
    });

    render(<EmployeesPage />);
    fireEvent.click(screen.getByRole("button", { name: "Ubaid Qureshi" }));
    fireEvent.click(await screen.findByRole("tab", { name: /Document Vault \(2\)/i }));

    expect(screen.getByText("Google Drive")).toBeInTheDocument();
    expect(screen.getByText("Local Vault")).toBeInTheDocument();
    expect(screen.getByText("Expires in 5d")).toBeInTheDocument();
    expect(screen.getByText("Expires in 20d")).toBeInTheDocument();

    // Download
    const dlButtons = screen.getAllByRole("button", { name: /Download/i });
    fireEvent.click(dlButtons[0]);
    await waitFor(() => {
      expect(mockDownloadDoc).toHaveBeenCalledWith({
        employeeId: "emp-1",
        documentId: "doc-1",
        fileName: "contract.pdf",
      });
    });

    // Delete
    fireEvent.click(screen.getByRole("button", { name: /Delete document Employment Contract/i }));
    await waitFor(() => {
      expect(mockDeleteDoc).toHaveBeenCalledWith("doc-1");
    });

    // Upload
    const fileInput = screen.getByLabelText(/File \(PDF, PNG, JPEG, DOCX/i);
    const pdf = new File(["pdf"], "cnic.pdf", { type: "application/pdf" });
    fireEvent.change(fileInput, { target: { files: [pdf] } });
    fireEvent.click(screen.getByRole("button", { name: /Upload Document/i }));
    await waitFor(() => {
      expect(mockUploadDoc).toHaveBeenCalledWith(
        expect.objectContaining({ documentType: "cnic", file: pdf })
      );
    });

    confirmSpy.mockRestore();
  });

  it("switches to Org Chart tab, renders root -> child hierarchy, direct reports count, and Unassigned bucket", () => {
    render(<EmployeesPage />);

    fireEvent.click(screen.getByRole("tab", { name: /Org Chart/i }));

    expect(screen.getByText(/Reporting Hierarchy \(3 Employees\)/i)).toBeInTheDocument();
    expect(screen.getByText("Ubaid Qureshi")).toBeInTheDocument();
    expect(screen.getByText("1 direct report")).toBeInTheDocument();
    expect(screen.getByText("Zainab Ali")).toBeInTheDocument();
    expect(screen.getByText(/Unassigned \/ No Manager \(1\)/i)).toBeInTheDocument();
    expect(screen.getByText("Orphan Contractor")).toBeInTheDocument();

    // Collapse root node hides child
    fireEvent.click(
      screen.getByRole("button", { name: /Collapse direct reports for Ubaid Qureshi/i })
    );
    expect(screen.queryByText("Zainab Ali")).not.toBeInTheDocument();
  });

  it("renders Header NotificationBell unread badge, opens popover, marks read, marks all read, and runs expiry scan", async () => {
    render(<Header />);

    expect(screen.getByTestId("notification-unread-badge")).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("button", { name: /Notifications \(1 unread\)/i }));

    expect(await screen.findByText("Security Clearance expires in 1 day")).toBeInTheDocument();
    expect(screen.getByText("10:00 PKT", { exact: false })).toBeInTheDocument();

    // Click item to mark read
    fireEvent.click(screen.getByText("Security Clearance expires in 1 day"));
    await waitFor(() => {
      expect(mockMarkRead).toHaveBeenCalledWith("notif-1");
    });

    // Click Mark all read
    fireEvent.click(screen.getByRole("button", { name: /Mark all read/i }));
    await waitFor(() => {
      expect(mockMarkAllRead).toHaveBeenCalledTimes(1);
    });

    // Click Run Expiry Scan
    fireEvent.click(screen.getByRole("button", { name: /Run Expiry Scan/i }));
    await waitFor(() => {
      expect(mockTriggerScan).toHaveBeenCalledTimes(1);
    });
  });
});
