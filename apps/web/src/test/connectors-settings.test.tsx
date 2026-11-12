import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import SettingsPage from "@/app/(dashboard)/settings/page";
import * as connectorsHook from "@/hooks/use-connectors";
import * as currentUserHook from "@/hooks/use-current-user";
import type { ConnectorResponse } from "@agentic-hr/contracts";

vi.mock("@/hooks/use-connectors");
vi.mock("@/hooks/use-current-user");

describe("SettingsPage - Connector Subsystem UI", () => {
  const mockToggleMutate = vi.fn();
  const mockRefreshMutate = vi.fn();
  const mockRevokeMutate = vi.fn();
  const mockReconnectMutate = vi.fn();
  const mockAuthorizeMutate = vi.fn();

  const baseConnector: ConnectorResponse = {
    id: "f63315da-87b3-4648-bb1f-eca593e60674",
    tenant_id: "b4ec4ca5-f07b-4f61-82a9-ab78b8252496",
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
    created_at: "2026-11-11T10:00:00Z",
    updated_at: "2026-11-11T10:00:00Z",
  };

  beforeEach(() => {
    vi.clearAllMocks();

    vi.spyOn(currentUserHook, "useCurrentUser").mockReturnValue({
      data: {
        user_id: "user-1",
        clerk_user_id: "clerk-1",
        email: "owner@test.com",
        name: "Test Owner",
        tenant_id: "b4ec4ca5-f07b-4f61-82a9-ab78b8252496",
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
      data: [baseConnector],
      isLoading: false,
      error: null,
    } as any);

    vi.spyOn(connectorsHook, "useToggleReadOnly").mockReturnValue({
      mutate: mockToggleMutate,
      isPending: false,
    } as any);

    vi.spyOn(connectorsHook, "useRefreshConnector").mockReturnValue({
      mutate: mockRefreshMutate,
      isPending: false,
    } as any);

    vi.spyOn(connectorsHook, "useRevokeConnector").mockReturnValue({
      mutate: mockRevokeMutate,
      isPending: false,
    } as any);

    vi.spyOn(connectorsHook, "useReconnectConnector").mockReturnValue({
      mutate: mockReconnectMutate,
      isPending: false,
    } as any);

    vi.spyOn(connectorsHook, "useGoogleAuthorize").mockReturnValue({
      mutate: mockAuthorizeMutate,
      isPending: false,
    } as any);
  });

  it("renders connected Google Workspace card with account email, scopes, and Connected badge", () => {
    render(<SettingsPage />);

    expect(screen.getByText("Google Workspace Connector (ADR 006)")).toBeInTheDocument();
    expect(screen.getByText("agentic.hr.test.ubaid.owner@gmail.com")).toBeInTheDocument();
    expect(screen.getByText("gmail.send")).toBeInTheDocument();
    expect(screen.getByText("drive.file")).toBeInTheDocument();
    expect(screen.getByText("calendar.events")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();

    const switchBtn = screen.getByRole("switch", { name: "Toggle Read-Only Mode" });
    expect(switchBtn).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByText("Read-Only Active")).not.toBeInTheDocument();
  });

  it("renders Read-Only Active badge and checked toggle state when is_read_only is true", () => {
    vi.spyOn(connectorsHook, "useConnectors").mockReturnValue({
      data: [{ ...baseConnector, is_read_only: true }],
      isLoading: false,
      error: null,
    } as any);

    render(<SettingsPage />);

    expect(screen.getByText("Read-Only Active")).toBeInTheDocument();
    const switchBtn = screen.getByRole("switch", { name: "Toggle Read-Only Mode" });
    expect(switchBtn).toHaveAttribute("aria-checked", "true");
  });

  it("renders Needs Reconnect badge and urgent alert banner when status is needs_reconnect", () => {
    vi.spyOn(connectorsHook, "useConnectors").mockReturnValue({
      data: [{ ...baseConnector, status: "needs_reconnect", jobs_paused: true }],
      isLoading: false,
      error: null,
    } as any);

    render(<SettingsPage />);

    expect(screen.getByText("Needs Reconnect")).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Authentication Expired (401 / invalid_grant)");
    expect(alert).toHaveTextContent("Automated outbound email jobs are paused");
  });

  it("renders Revoked badge when connector status is revoked", () => {
    vi.spyOn(connectorsHook, "useConnectors").mockReturnValue({
      data: [{ ...baseConnector, status: "revoked" }],
      isLoading: false,
      error: null,
    } as any);

    render(<SettingsPage />);

    expect(screen.getByText("Revoked")).toBeInTheDocument();
  });

  it("clicking the read-only toggle triggers the toggleReadOnly mutation", () => {
    render(<SettingsPage />);

    const switchBtn = screen.getByRole("switch", { name: "Toggle Read-Only Mode" });
    fireEvent.click(switchBtn);

    expect(mockToggleMutate).toHaveBeenCalledTimes(1);
    expect(mockToggleMutate).toHaveBeenCalledWith(
      {
        credentialId: baseConnector.id,
        isReadOnly: true,
      },
      expect.any(Object)
    );
  });

  it("clicking Refresh Token button triggers the refresh mutation", () => {
    render(<SettingsPage />);

    const refreshBtn = screen.getByRole("button", { name: /Refresh Token/i });
    fireEvent.click(refreshBtn);

    expect(mockRefreshMutate).toHaveBeenCalledTimes(1);
    expect(mockRefreshMutate).toHaveBeenCalledWith(baseConnector.id, expect.any(Object));
  });

  it("renders Refreshing... loading state when refresh mutation is pending", () => {
    vi.spyOn(connectorsHook, "useRefreshConnector").mockReturnValue({
      mutate: mockRefreshMutate,
      isPending: true,
    } as any);

    render(<SettingsPage />);
    expect(screen.getByText("Refreshing...")).toBeInTheDocument();
  });

  it("clicking Disconnect button prompts confirm dialog and triggers revoke mutation", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<SettingsPage />);

    const disconnectBtn = screen.getByRole("button", { name: /Disconnect/i });
    fireEvent.click(disconnectBtn);

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(mockRevokeMutate).toHaveBeenCalledTimes(1);
    expect(mockRevokeMutate).toHaveBeenCalledWith(baseConnector.id);
    confirmSpy.mockRestore();
  });

  it("clicking Reconnect OAuth triggers reconnect mutation", () => {
    render(<SettingsPage />);

    const reconnectBtn = screen.getByRole("button", { name: /Reconnect OAuth/i });
    fireEvent.click(reconnectBtn);

    expect(mockReconnectMutate).toHaveBeenCalledTimes(1);
    expect(mockReconnectMutate).toHaveBeenCalledWith(baseConnector.id, expect.any(Object));
  });

  it("guarantees no raw or encrypted tokens are rendered in the HTML", () => {
    const { container } = render(<SettingsPage />);
    const html = container.innerHTML;

    expect(html).not.toContain("access_token");
    expect(html).not.toContain("refresh_token");
    expect(html).not.toContain("ya29.");
    expect(html).not.toContain("AES-256-GCM$");
  });
});
