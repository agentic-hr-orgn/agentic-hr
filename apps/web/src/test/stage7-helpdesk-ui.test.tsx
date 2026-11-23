import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type {
  EmployeeSummary,
  Ticket,
  TicketAssignmentRule,
  TicketDetail,
} from "@agentic-hr/contracts";
import HelpdeskPage from "@/app/(dashboard)/helpdesk/page";
import { ToastProvider } from "@/components/ui/toast-provider";
import * as backendStatusHook from "@/hooks/use-backend-status";
import * as currentUserHook from "@/hooks/use-current-user";
import * as employeesHook from "@/hooks/use-employees";
import * as ticketsHook from "@/hooks/use-tickets";
import { ApiHttpError, ApiOfflineError } from "@/lib/api-request";
import { useToastStore } from "@/stores/toast-store";

const mockSearchParams = vi.fn<() => URLSearchParams>(() => new URLSearchParams());

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/helpdesk",
}));

vi.mock("@/hooks/use-backend-status");
vi.mock("@/hooks/use-current-user");
vi.mock("@/hooks/use-tickets");
vi.mock("@/hooks/use-employees");

function makeTicket(overrides: Partial<Ticket> = {}): Ticket {
  return {
    id: "tkt-1",
    tenant_id: "t-acme",
    ticket_number: "TKT-2026-0001",
    requester_user_id: "u-emp",
    assigned_to_user_id: "u-hr",
    category: "hr_policy",
    priority: "medium",
    status: "open",
    subject: "Clarify annual leave encashment",
    description: "Can unused leave be encashed at year end?",
    source: "manual",
    source_session_id: null,
    sla_due_at: "2099-11-24T17:00:00Z",
    sla_reminder_at: "2099-11-24T13:00:00Z",
    sla_reminder_sent_at: null,
    sla_escalated_at: null,
    resolved_at: null,
    closed_at: null,
    reopen_until: null,
    satisfaction_rating: null,
    satisfaction_comment: null,
    created_at: "2026-11-23T09:00:00Z",
    updated_at: "2026-11-23T09:00:00Z",
    ...overrides,
  };
}

function makeTicketDetail(overrides: Partial<TicketDetail> = {}): TicketDetail {
  return {
    ...makeTicket(overrides),
    messages: [],
    ...overrides,
  };
}

const SAMPLE_STAFF: EmployeeSummary[] = [
  {
    id: "emp-hr",
    tenant_id: "t-acme",
    user_id: "u-hr",
    employee_code: "ACM-002",
    full_name: "Ayesha HR",
    email: "hr@acme.internal",
    phone: null,
    department_id: null,
    department_name: "HR",
    manager_id: null,
    manager_name: null,
    job_title: "HR Head",
    employment_type: "full_time",
    join_date: "2024-01-10",
    leaving_date: null,
    status: "active",
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
  },
];

const SAMPLE_RULES: TicketAssignmentRule[] = [
  {
    id: "rule-hr",
    tenant_id: "t-acme",
    category: "hr_policy",
    default_assignee_user_id: "u-hr",
    default_assignee_role: "hr_head",
    sla_hours: 24,
    reminder_before_hours: 4,
    reopen_window_days: 7,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
  },
];

describe("Stage 7 Prompt 7 — /helpdesk Workspace & Ticketing UI", () => {
  const createTicketMutateAsync = vi.fn();
  const updateTicketMutateAsync = vi.fn();
  const addMessageMutateAsync = vi.fn();
  const rateTicketMutateAsync = vi.fn();
  const reopenTicketMutateAsync = vi.fn();
  const updateRuleMutateAsync = vi.fn();
  const askHelpdeskMutateAsync = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    mockSearchParams.mockReturnValue(new URLSearchParams());
    act(() => {
      useToastStore.getState().clearToasts();
    });

    vi.mocked(backendStatusHook.useBackendStatus).mockReturnValue({
      status: "online",
      isOnline: true,
      isStandby: false,
      useFakeIntegrations: true,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof backendStatusHook.useBackendStatus>);

    vi.mocked(currentUserHook.useCurrentUser).mockReturnValue({
      data: {
        user_id: "u-emp",
        clerk_user_id: "clerk_emp",
        email: "emp@acme.internal",
        name: "Bilal Employee",
        tenant_id: "t-acme",
        clerk_org_id: "org_acme_tech_01",
        tenant_name: "Acme Tech Solutions",
        role: "employee",
        is_hr_head: false,
        is_payroll_admin: false,
        permissions: [],
        is_tenant_suspended: false,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof currentUserHook.useCurrentUser>);

    vi.mocked(employeesHook.useEmployees).mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 50, total_pages: 0 },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof employeesHook.useEmployees>);

    vi.mocked(ticketsHook.useMyTickets).mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof ticketsHook.useMyTickets>);

    vi.mocked(ticketsHook.useStaffTicketQueue).mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof ticketsHook.useStaffTicketQueue>);

    vi.mocked(ticketsHook.useTicketDetail).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof ticketsHook.useTicketDetail>);

    vi.mocked(ticketsHook.useTicketRules).mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof ticketsHook.useTicketRules>);

    vi.mocked(ticketsHook.useCreateTicket).mockReturnValue({
      mutateAsync: createTicketMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof ticketsHook.useCreateTicket>);

    vi.mocked(ticketsHook.useUpdateTicket).mockReturnValue({
      mutateAsync: updateTicketMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof ticketsHook.useUpdateTicket>);

    vi.mocked(ticketsHook.useAddTicketMessage).mockReturnValue({
      mutateAsync: addMessageMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof ticketsHook.useAddTicketMessage>);

    vi.mocked(ticketsHook.useRateTicket).mockReturnValue({
      mutateAsync: rateTicketMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof ticketsHook.useRateTicket>);

    vi.mocked(ticketsHook.useReopenTicket).mockReturnValue({
      mutateAsync: reopenTicketMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof ticketsHook.useReopenTicket>);

    vi.mocked(ticketsHook.useUpdateTicketRule).mockReturnValue({
      mutateAsync: updateRuleMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof ticketsHook.useUpdateTicketRule>);

    vi.mocked(ticketsHook.useAskHelpdesk).mockReturnValue({
      mutateAsync: askHelpdeskMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof ticketsHook.useAskHelpdesk>);
  });

  it("renders grounded KB citations and auto-created ticket banners in HelpdeskAskCard", async () => {
    askHelpdeskMutateAsync
      .mockResolvedValueOnce({
        answered: true,
        answer: "Reset your VPN password via portal.acme.local using SSO.",
        language: "en",
        citations: [
          {
            chunk_id: "kb-chk-1",
            document_id: "doc-kb-1",
            title: "Corporate VPN Reset Guide",
            version: 1,
            section: "KB-IT-01",
            page: 1,
            snippet: "Open SSO Self-Service Portal at portal.acme.local.",
          },
        ],
        ticket: null,
        ticket_number: null,
        category: null,
        priority: null,
        privileged_request_blocked: false,
      })
      .mockResolvedValueOnce({
        answered: false,
        answer: "Created ticket TKT-2026-0044 for IT hardware support.",
        language: "en",
        citations: [],
        ticket: makeTicket({
          id: "tkt-44",
          ticket_number: "TKT-2026-0044",
          category: "it_access",
          priority: "high",
        }),
        ticket_number: "TKT-2026-0044",
        category: "it_access",
        priority: "high",
        privileged_request_blocked: false,
      });

    render(<HelpdeskPage />);

    const askInput = screen.getByLabelText("Ask Helpdesk a question");
    fireEvent.change(askInput, { target: { value: "How do I reset my corporate VPN password?" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Helpdesk/i }));

    expect(
      await screen.findByText(/Reset your VPN password via portal\.acme\.local/i)
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Corporate VPN Reset Guide/i })).toHaveAttribute(
      "href",
      "/policies/doc-kb-1/preview?page=1&chunk=kb-chk-1"
    );

    fireEvent.change(askInput, { target: { value: "My laptop charger is broken" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask Helpdesk/i }));

    expect(await screen.findByText(/Ticket Created: TKT-2026-0044/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open Ticket Detail/i })).toBeInTheDocument();
  });

  it("supports employee ?ticket deep-link, 1-5 rating, reopen, hides internal notes/tabs & fires zero 403s", async () => {
    mockSearchParams.mockReturnValue(new URLSearchParams("ticket=tkt-deep-9"));
    rateTicketMutateAsync.mockResolvedValue(
      makeTicket({ id: "tkt-deep-9", satisfaction_rating: 4 })
    );
    reopenTicketMutateAsync.mockResolvedValue(makeTicket({ id: "tkt-deep-9", status: "open" }));

    vi.mocked(ticketsHook.useMyTickets).mockReturnValue({
      data: [
        makeTicket({
          id: "tkt-deep-9",
          ticket_number: "TKT-2026-0009",
          status: "resolved",
          reopen_until: "2099-12-01T00:00:00Z",
        }),
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof ticketsHook.useMyTickets>);

    vi.mocked(ticketsHook.useTicketDetail).mockReturnValue({
      data: makeTicketDetail({
        id: "tkt-deep-9",
        ticket_number: "TKT-2026-0009",
        status: "resolved",
        reopen_until: "2099-12-01T00:00:00Z",
        messages: [
          {
            id: "m-public",
            tenant_id: "t-acme",
            ticket_id: "tkt-deep-9",
            author_user_id: "u-hr",
            body: "Public resolution note for employee.",
            is_internal: false,
            created_at: "2026-11-23T10:00:00Z",
          },
          {
            id: "m-secret",
            tenant_id: "t-acme",
            ticket_id: "tkt-deep-9",
            author_user_id: "u-hr",
            body: "Hidden internal staff-only escalation diagnostic.",
            is_internal: true,
            created_at: "2026-11-23T10:05:00Z",
          },
        ],
      }),
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof ticketsHook.useTicketDetail>);

    render(
      <>
        <HelpdeskPage />
        <ToastProvider />
      </>
    );

    // Zero background 403s: staff hooks & employee directory disabled for plain Employee
    expect(ticketsHook.useStaffTicketQueue).toHaveBeenCalledWith(expect.any(Object), {
      enabled: false,
    });
    expect(ticketsHook.useTicketRules).toHaveBeenCalledWith({ enabled: false });
    expect(employeesHook.useEmployees).toHaveBeenCalledWith({ pageSize: 50, enabled: false });

    expect(screen.queryByRole("tab", { name: /Staff Queue/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Assignment Rules/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Internal Note/i)).not.toBeInTheDocument();
    expect(screen.getByText("Public resolution note for employee.")).toBeInTheDocument();
    expect(
      screen.queryByText("Hidden internal staff-only escalation diagnostic.")
    ).not.toBeInTheDocument();

    expect(ticketsHook.useTicketDetail).toHaveBeenCalledWith("tkt-deep-9", { enabled: true });

    // Submit 4-star CSAT rating
    fireEvent.click(screen.getByRole("button", { name: "Rate 4 stars" }));
    fireEvent.change(screen.getByLabelText("Satisfaction comment"), {
      target: { value: "Helpful response" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Submit Rating/i }));

    await waitFor(() => {
      expect(rateTicketMutateAsync).toHaveBeenCalledWith({
        ticketId: "tkt-deep-9",
        payload: { satisfaction_rating: 4, satisfaction_comment: "Helpful response" },
      });
    });

    // Reopen within reopen window
    fireEvent.change(screen.getByPlaceholderText(/Reason for reopening/i), {
      target: { value: "Need written payroll breakdown" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Reopen Ticket/i }));

    await waitFor(() => {
      expect(reopenTicketMutateAsync).toHaveBeenCalledWith({
        ticketId: "tkt-deep-9",
        payload: { reason: "Need written payroll breakdown" },
      });
    });
  });

  it("opens NewTicketModal, submits ticket, and triggers toast with ticket_number", async () => {
    createTicketMutateAsync.mockResolvedValue(
      makeTicket({
        id: "tkt-new-77",
        ticket_number: "TKT-2026-0077",
        category: "facilities",
        priority: "medium",
        subject: "Fix conference room AC",
      })
    );

    render(
      <>
        <HelpdeskPage />
        <ToastProvider />
      </>
    );

    fireEvent.click(screen.getByRole("button", { name: /\+ New Ticket/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "facilities" } });
    fireEvent.change(screen.getByLabelText("Subject"), {
      target: { value: "Fix conference room AC" },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Second floor AC unit is blowing warm air." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Submit Ticket/i }));

    await waitFor(() => {
      expect(createTicketMutateAsync).toHaveBeenCalledWith({
        category: "facilities",
        priority: "medium",
        subject: "Fix conference room AC",
        description: "Second floor AC unit is blowing warm air.",
        source: "manual",
      });
    });

    expect(await screen.findByText(/Ticket Created \(TKT-2026-0077\)/i)).toBeInTheDocument();
  });

  it("supports Staff Queue filters, internal staff notes, WAI-ARIA tab keyboard nav, and Assignment Rules updates", async () => {
    addMessageMutateAsync.mockResolvedValue({
      id: "m-int-1",
      tenant_id: "t-acme",
      ticket_id: "tkt-q1",
      author_user_id: "u-hr",
      body: "Checking payroll ledger before replying.",
      is_internal: true,
      created_at: "2026-11-23T11:00:00Z",
    });
    updateRuleMutateAsync.mockResolvedValue(SAMPLE_RULES[0]);

    vi.mocked(currentUserHook.useCurrentUser).mockReturnValue({
      data: {
        user_id: "u-hr",
        clerk_user_id: "clerk_hr",
        email: "hr@acme.internal",
        name: "Ayesha HR",
        tenant_id: "t-acme",
        clerk_org_id: "org_acme_tech_01",
        tenant_name: "Acme Tech Solutions",
        role: "manager",
        is_hr_head: true,
        is_payroll_admin: false,
        permissions: ["tickets.manage"],
        is_tenant_suspended: false,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof currentUserHook.useCurrentUser>);

    vi.mocked(employeesHook.useEmployees).mockReturnValue({
      data: { items: SAMPLE_STAFF, total: 1, page: 1, page_size: 50, total_pages: 1 },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof employeesHook.useEmployees>);

    vi.mocked(ticketsHook.useStaffTicketQueue).mockReturnValue({
      data: [
        makeTicket({
          id: "tkt-q1",
          ticket_number: "TKT-2026-0011",
          subject: "Salary slip tax deduction mismatch",
          category: "payroll",
          priority: "urgent",
          sla_escalated_at: "2026-11-23T08:00:00Z",
        }),
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof ticketsHook.useStaffTicketQueue>);

    vi.mocked(ticketsHook.useTicketDetail).mockReturnValue({
      data: makeTicketDetail({
        id: "tkt-q1",
        ticket_number: "TKT-2026-0011",
        subject: "Salary slip tax deduction mismatch",
        category: "payroll",
        priority: "urgent",
        sla_escalated_at: "2026-11-23T08:00:00Z",
        messages: [
          {
            id: "m-int-existing",
            tenant_id: "t-acme",
            ticket_id: "tkt-q1",
            author_user_id: "u-hr",
            body: "Verified FBR tax slab internally.",
            is_internal: true,
            created_at: "2026-11-23T10:30:00Z",
          },
        ],
      }),
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof ticketsHook.useTicketDetail>);

    vi.mocked(ticketsHook.useTicketRules).mockReturnValue({
      data: SAMPLE_RULES,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof ticketsHook.useTicketRules>);

    render(
      <>
        <HelpdeskPage />
        <ToastProvider />
      </>
    );

    expect(employeesHook.useEmployees).toHaveBeenCalledWith({ pageSize: 50, enabled: true });

    // WAI-ARIA ArrowRight from My Tickets -> Staff Queue
    const myTab = screen.getByRole("tab", { name: /^My Tickets/i });
    fireEvent.keyDown(myTab, { key: "ArrowRight" });
    const queueTab = screen.getByRole("tab", { name: /^Staff Queue/i });
    expect(queueTab).toHaveAttribute("aria-selected", "true");

    expect(screen.getAllByText("SLA Breached").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Internal Only")).toBeInTheDocument();
    expect(screen.getByText("Verified FBR tax slab internally.")).toBeInTheDocument();

    // Filter queue search
    fireEvent.change(screen.getByLabelText("Search staff queue"), {
      target: { value: "TKT-2026-0011" },
    });
    expect(screen.getAllByText("Salary slip tax deduction mismatch").length).toBeGreaterThanOrEqual(
      1
    );

    // Post internal note
    fireEvent.click(screen.getByLabelText(/Internal Note \(Staff Only\)/i));
    fireEvent.change(screen.getByLabelText("Ticket reply message"), {
      target: { value: "Checking payroll ledger before replying." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add Internal Note/i }));

    await waitFor(() => {
      expect(addMessageMutateAsync).toHaveBeenCalledWith({
        ticketId: "tkt-q1",
        payload: {
          body: "Checking payroll ledger before replying.",
          is_internal: true,
        },
      });
    });

    // Switch to Assignment Rules tab & update HR Policy rule
    fireEvent.click(screen.getByRole("tab", { name: /^Assignment Rules/i }));
    fireEvent.change(screen.getByLabelText("HR Policy SLA hours"), {
      target: { value: "16" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save HR Policy rule" }));

    await waitFor(() => {
      expect(updateRuleMutateAsync).toHaveBeenCalledWith({
        category: "hr_policy",
        payload: {
          default_assignee_role: "hr_head",
          default_assignee_user_id: "u-hr",
          sla_hours: 16,
          reminder_before_hours: 4,
          reopen_window_days: 7,
        },
      });
    });
  });

  it("shows StandbyBanner strictly on ApiOfflineError and renders ApiHttpError in role=alert", () => {
    vi.mocked(ticketsHook.useMyTickets).mockReturnValue({
      data: [],
      isLoading: false,
      error: new ApiHttpError(409, "CONFLICT", "Live Helpdesk conflict error"),
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof ticketsHook.useMyTickets>);

    const { rerender } = render(<HelpdeskPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("Live Helpdesk conflict error");
    expect(screen.queryByText(/FastAPI Backend in Standby Mode/i)).not.toBeInTheDocument();

    vi.mocked(ticketsHook.useMyTickets).mockReturnValue({
      data: [],
      isLoading: false,
      error: new ApiOfflineError("OFFLINE: Backend down."),
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof ticketsHook.useMyTickets>);

    rerender(<HelpdeskPage />);
    expect(screen.getByText(/FastAPI Backend in Standby Mode/i)).toBeInTheDocument();
  });
});
