import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import PoliciesPage from "@/app/(dashboard)/policies/page";
import { ToastProvider } from "@/components/ui/toast-provider";
import { EmployeeProfileDrawer } from "@/components/employees/employee-profile-drawer";
import { useToastStore } from "@/stores/toast-store";
import * as backendStatusHook from "@/hooks/use-backend-status";
import * as currentUserHook from "@/hooks/use-current-user";
import * as policiesHook from "@/hooks/use-policies";
import * as employeesHook from "@/hooks/use-employees";

vi.mock("@/hooks/use-backend-status");
vi.mock("@/hooks/use-current-user");
vi.mock("@/hooks/use-policies");
vi.mock("@/hooks/use-employees");

describe("Pre-Stage 5 Enterprise Hardening UI (Live Policies, Toasts & Drawer Focus)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useToastStore.getState().clearToasts();

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
        clerk_user_id: "user_owner",
        email: "owner@acme.internal",
        name: "Acme Owner",
        tenant_id: "t-acme",
        clerk_org_id: "org_acme",
        tenant_name: "Acme Tech Solutions",
        role: "owner",
        is_hr_head: true,
        is_payroll_admin: true,
        permissions: ["owner"],
        is_tenant_suspended: false,
      },
      isLoading: false,
      error: null,
    } as any);
  });

  it("PoliciesPage displays live uploaded policies, uploads a PDF, deletes a policy, and triggers toasts", async () => {
    const uploadMutateAsync = vi.fn().mockResolvedValue({
      id: "pol-new",
      title: "Travel Policy 2026",
      file_name: "travel.pdf",
      mime_type: "application/pdf",
      size_bytes: 2048,
      ingestion_status: "queued_stub",
      created_at: "2026-11-21T10:00:00+05:00",
    });
    const deleteMutateAsync = vi.fn().mockResolvedValue({ status: "deleted", id: "pol-1" });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    vi.spyOn(policiesHook, "usePolicies").mockReturnValue({
      data: [
        {
          id: "pol-1",
          tenant_id: "t-acme",
          title: "Leave Handbook",
          file_name: "leave.pdf",
          mime_type: "application/pdf",
          size_bytes: 1024,
          storage_provider: "local",
          storage_key: "t-acme/policies/leave.pdf",
          ingestion_status: "queued_stub",
          created_at: "2026-11-20T09:00:00+05:00",
          updated_at: "2026-11-20T09:00:00+05:00",
        },
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as any);

    vi.spyOn(policiesHook, "useUploadPolicy").mockReturnValue({
      mutateAsync: uploadMutateAsync,
      isPending: false,
    } as any);

    vi.spyOn(policiesHook, "useDeletePolicy").mockReturnValue({
      mutateAsync: deleteMutateAsync,
      isPending: false,
    } as any);

    render(
      <>
        <PoliciesPage />
        <ToastProvider />
      </>
    );

    expect(screen.getByText(/Uploaded Corporate Policies \(1\)/i)).toBeInTheDocument();
    expect(screen.getByText("Leave Handbook")).toBeInTheDocument();

    const fileInput = screen.getByLabelText(/Document File/i) as HTMLInputElement;
    const pdfFile = new File(["%PDF-1.4 content"], "travel.pdf", { type: "application/pdf" });
    fireEvent.change(fileInput, { target: { files: [pdfFile] } });
    fireEvent.change(screen.getByLabelText(/Policy Title/i), {
      target: { value: "Travel Policy 2026" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Upload Policy$/i }));

    await waitFor(() => {
      expect(uploadMutateAsync).toHaveBeenCalledWith({
        file: pdfFile,
        title: "Travel Policy 2026",
      });
    });
    expect(await screen.findByText("Policy uploaded")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Delete policy Leave Handbook/i }));
    await waitFor(() => {
      expect(deleteMutateAsync).toHaveBeenCalledWith("pol-1");
    });
    expect(await screen.findByText("Policy deleted")).toBeInTheDocument();
  });

  it("PoliciesPage renders StandbyBanner when FastAPI backend is offline", () => {
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

    vi.spyOn(policiesHook, "usePolicies").mockReturnValue({
      data: [],
      isLoading: false,
      error: new Error("OFFLINE: FastAPI backend is unreachable."),
      refetch: vi.fn(),
      isFetching: false,
    } as any);
    vi.spyOn(policiesHook, "useUploadPolicy").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(policiesHook, "useDeletePolicy").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    render(<PoliciesPage />);
    expect(
      screen.getByText(/Corporate Policy uploads and deletions require a live backend/i)
    ).toBeInTheDocument();
  });

  it("ToastProvider renders notifications and dismisses them on click", async () => {
    render(<ToastProvider />);
    useToastStore.getState().pushToast({
      variant: "error",
      title: "Sync Alert",
      description: "Connection expired",
    });

    expect(await screen.findByText("Sync Alert")).toBeInTheDocument();
    expect(screen.getByText("Connection expired")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    expect(screen.queryByText("Sync Alert")).not.toBeInTheDocument();
  });

  it("EmployeeProfileDrawer returns keyboard focus to opener element when closed", () => {
    vi.spyOn(employeesHook, "useEmployeeProfile").mockReturnValue({
      data: {
        id: "emp-1",
        tenant_id: "t-acme",
        full_name: "Ubaid Qureshi",
        email: "owner@acme.internal",
        job_title: "CEO",
        employment_type: "full_time",
        join_date: "2024-01-01",
        status: "active",
        created_at: "2024-01-01T10:00:00+05:00",
        updated_at: "2024-01-01T10:00:00+05:00",
        can_view_sensitive: true,
        can_edit_sensitive: true,
        cnic: "42101-1234567-1",
        bank_account: "PK36HABB0000111122223333",
      },
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(employeesHook, "useUpdateEmployee").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(employeesHook, "useEmployeeDocuments").mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
    } as any);
    vi.spyOn(employeesHook, "useUploadEmployeeDocument").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(employeesHook, "useDownloadEmployeeDocument").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);
    vi.spyOn(employeesHook, "useDeleteEmployeeDocument").mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as any);

    function Harness() {
      const [openId, setOpenId] = React.useState<string | null>(null);
      return (
        <div>
          <button type="button" onClick={() => setOpenId("emp-1")}>
            Open Drawer Trigger
          </button>
          <EmployeeProfileDrawer
            employeeId={openId}
            onClose={() => setOpenId(null)}
            departments={[]}
            managerOptions={[]}
          />
        </div>
      );
    }

    render(<Harness />);
    const triggerBtn = screen.getByRole("button", { name: "Open Drawer Trigger" });
    triggerBtn.focus();
    fireEvent.click(triggerBtn);

    const dialog = screen.getByRole("dialog");
    expect(document.activeElement).toBe(dialog);

    fireEvent.click(screen.getByRole("button", { name: "Close employee profile drawer" }));
    expect(document.activeElement).toBe(triggerBtn);
  });
});
