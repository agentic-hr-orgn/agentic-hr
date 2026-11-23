import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ChatMessage, ChatSession, PolicyGapsResponse } from "@agentic-hr/contracts";
import PolicyChatPage from "@/app/(dashboard)/chat/page";
import PolicyGapsPage from "@/app/(dashboard)/policy-gaps/page";
import { resolveRouteTitle } from "@/components/layout/header";
import { ToastProvider } from "@/components/ui/toast-provider";
import * as backendStatusHook from "@/hooks/use-backend-status";
import * as chatHook from "@/hooks/use-chat";
import * as currentUserHook from "@/hooks/use-current-user";
import { ApiHttpError, ApiOfflineError } from "@/lib/api-request";
import { useToastStore } from "@/stores/toast-store";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/chat",
}));

vi.mock("@/hooks/use-backend-status");
vi.mock("@/hooks/use-current-user");
vi.mock("@/hooks/use-chat");

function makeSession(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    id: "sess-1",
    tenant_id: "t-acme",
    user_id: "u-emp",
    title: "Annual Leave Policy",
    language: "en",
    last_message_at: "2026-11-23T15:00:00Z",
    created_at: "2026-11-23T14:55:00Z",
    updated_at: "2026-11-23T15:00:00Z",
    ...overrides,
  };
}

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "msg-1",
    tenant_id: "t-acme",
    session_id: "sess-1",
    role: "assistant",
    content: "Employees receive 20 paid annual leave days per calendar year.",
    language: "en",
    intent: "policy_qa",
    citations: [],
    tool_calls: [],
    abstained: false,
    ticket_id: null,
    ticket_number: null,
    handoff_payload: null,
    topic_tag: "leave_policy",
    latency_ms: 42,
    model_tier: "strong",
    token_usage: { prompt_tokens: 120, completion_tokens: 30, total_tokens: 150 },
    cost_usd: "0.00012",
    created_at: "2026-11-23T15:00:05Z",
    ...overrides,
  };
}

describe("Stage 7 Prompt 6 — /chat Policy Brain Workspace & /policy-gaps Dashboard", () => {
  const createSessionMutateAsync = vi.fn();
  const deleteSessionMutateAsync = vi.fn();
  const feedbackMutateAsync = vi.fn();
  const askHrMutateAsync = vi.fn();
  const streamChatMock = vi.fn();
  const abortMock = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
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
        permissions: ["policies.manage"],
        is_tenant_suspended: false,
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof currentUserHook.useCurrentUser>);

    vi.mocked(chatHook.useCreateChatSession).mockReturnValue({
      mutateAsync: createSessionMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof chatHook.useCreateChatSession>);

    vi.mocked(chatHook.useDeleteChatSession).mockReturnValue({
      mutateAsync: deleteSessionMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof chatHook.useDeleteChatSession>);

    vi.mocked(chatHook.useSubmitAnswerFeedback).mockReturnValue({
      mutateAsync: feedbackMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof chatHook.useSubmitAnswerFeedback>);

    vi.mocked(chatHook.useAskHrFromMessage).mockReturnValue({
      mutateAsync: askHrMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof chatHook.useAskHrFromMessage>);

    vi.mocked(chatHook.useStreamChat).mockReturnValue({
      streamChat: streamChatMock,
      isStreaming: false,
      abort: abortMock,
    } as unknown as ReturnType<typeof chatHook.useStreamChat>);

    vi.mocked(chatHook.useChatSessions).mockReturnValue({
      data: [makeSession()],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.useChatSessions>);

    vi.mocked(chatHook.useChatMessages).mockReturnValue({
      data: [],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.useChatMessages>);

    vi.mocked(chatHook.usePolicyGaps).mockReturnValue({
      data: { total_gaps: 0, groups: [] },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.usePolicyGaps>);
  });

  it("streams answer tokens via apiStreamChat hook, renders citation deep-links & leave handoff cards", async () => {
    streamChatMock.mockImplementation(async ({ onEvent }) => {
      onEvent?.({ type: "status", data: { node: "retrieve" } });
      onEvent?.({ type: "token", data: { text: "Full-time staff get 20 annual leave days." } });
      onEvent?.({
        type: "done",
        data: {
          message_id: "msg-stream-1",
          session_id: "sess-1",
          answer: "Full-time staff get 20 annual leave days.",
          language: "en",
          intent: "policy_qa",
          citations: [
            {
              chunk_id: "chunk-99",
              document_id: "doc-pol-1",
              title: "Leave Policy 2026",
              version: 2,
              section: "Section 1.2",
              page: 3,
              snippet: "Full-time employees receive 20 paid annual leave days.",
            },
          ],
          tool_calls: [],
          abstained: false,
          ticket_id: null,
          ticket_number: null,
          handoff_payload: {
            status: "ready_to_confirm",
            extracted: {
              leave_type_code: "annual",
              start_date: "2026-12-07",
              end_date: "2026-12-08",
            },
            confirm_card: {
              leave_type_name: "Annual Leave",
              working_days: 2,
            },
            prefill_url: "/leave",
          },
          topic_tag: "leave_policy",
          latency_ms: 55,
        },
      });
      return {
        aborted: false,
        donePayload: { message_id: "msg-stream-1" },
      };
    });

    render(<PolicyChatPage />);

    const input = screen.getByLabelText("Ask Policy Brain a question");
    fireEvent.change(input, { target: { value: "How many annual leave days do I get?" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText("Full-time staff get 20 annual leave days.")).toBeInTheDocument();
    });

    const citationLink = screen.getByRole("link", { name: /Leave Policy 2026/i });
    expect(citationLink).toHaveAttribute(
      "href",
      "/policies/doc-pol-1/preview?page=3&chunk=chunk-99"
    );

    expect(screen.getByText("Leave Request Handoff")).toBeInTheDocument();
    expect(screen.getByText("Annual Leave")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Continue in Leave Workspace/i })).toHaveAttribute(
      "href",
      "/leave"
    );
  });

  it("displays abstained ticket link, Ask HR escalation button, and ToastProvider ticket link", async () => {
    askHrMutateAsync.mockResolvedValue({
      ticket_id: "tkt-uuid-22",
      ticket_number: "TKT-2026-0022",
      category: "hr_policy",
      priority: "medium",
      message_id: "msg-normal-2",
    });

    vi.mocked(chatHook.useChatMessages).mockReturnValue({
      data: [
        makeMessage({
          id: "msg-abstain-1",
          content: "Could not find an approved policy answer. Ticket TKT-2026-0010 created.",
          abstained: true,
          ticket_id: "tkt-uuid-10",
          ticket_number: "TKT-2026-0010",
        }),
        makeMessage({
          id: "msg-normal-2",
          content: "Standard office timings are 09:00 to 18:00.",
          abstained: false,
          ticket_id: null,
          ticket_number: null,
        }),
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.useChatMessages>);

    render(
      <>
        <PolicyChatPage />
        <ToastProvider />
      </>
    );

    const abstainTicketLink = screen.getByRole("link", { name: /TKT-2026-0010/i });
    expect(abstainTicketLink).toHaveAttribute("href", "/helpdesk?ticket=tkt-uuid-10");

    const askHrBtn = screen.getByRole("button", { name: /Ask HR/i });
    fireEvent.click(askHrBtn);

    await waitFor(() => {
      expect(askHrMutateAsync).toHaveBeenCalledWith({ messageId: "msg-normal-2" });
    });

    expect(await screen.findByText(/HR Ticket Created \(TKT-2026-0022\)/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open Helpdesk/i })).toHaveAttribute(
      "href",
      "/helpdesk?ticket=tkt-uuid-22"
    );
  });

  it("submits Thumbs Up and Thumbs Down with optional comment feedback", async () => {
    feedbackMutateAsync.mockResolvedValue({
      id: "fb-1",
      tenant_id: "t-acme",
      message_id: "msg-fb-1",
      user_id: "u-emp",
      rating: "up",
      created_at: "2026-11-23T15:01:00Z",
    });

    vi.mocked(chatHook.useChatMessages).mockReturnValue({
      data: [
        makeMessage({
          id: "msg-fb-1",
          content: "Employees receive 10 sick leave days.",
        }),
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.useChatMessages>);

    render(<PolicyChatPage />);

    fireEvent.click(screen.getByRole("button", { name: "Thumbs Up" }));
    await waitFor(() => {
      expect(feedbackMutateAsync).toHaveBeenCalledWith({
        messageId: "msg-fb-1",
        payload: { rating: "up", comment: null },
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "Thumbs Down" }));
    const commentInput = screen.getByLabelText("Feedback comment");
    fireEvent.change(commentInput, { target: { value: "Missing probation details" } });
    fireEvent.click(screen.getByRole("button", { name: /Submit Feedback/i }));

    await waitFor(() => {
      expect(feedbackMutateAsync).toHaveBeenCalledWith({
        messageId: "msg-fb-1",
        payload: { rating: "down", comment: "Missing probation details" },
      });
    });
  });

  it("supports switching chat sessions, creating/deleting chats, and dir=auto Urdu input", async () => {
    createSessionMutateAsync.mockResolvedValue(
      makeSession({ id: "sess-3", title: "New conversation" })
    );
    deleteSessionMutateAsync.mockResolvedValue(null);

    vi.mocked(chatHook.useChatSessions).mockReturnValue({
      data: [
        makeSession({
          id: "sess-1",
          title: "Leave Policy",
          last_message_at: "2026-11-23T15:10:00Z",
        }),
        makeSession({
          id: "sess-2",
          title: "بیماری کی پالیسی",
          language: "ur",
          last_message_at: "2026-11-23T14:00:00Z",
        }),
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.useChatSessions>);

    render(<PolicyChatPage />);

    const secondSessionBtn = screen.getByRole("button", { name: /^بیماری کی پالیسی/i });
    fireEvent.click(secondSessionBtn);
    expect(abortMock).toHaveBeenCalled();

    const textarea = screen.getByLabelText("Ask Policy Brain a question");
    expect(textarea).toHaveAttribute("dir", "auto");
    fireEvent.change(textarea, { target: { value: "بیماری کی کتنی چھٹیاں ملتی ہیں؟" } });
    expect(textarea).toHaveValue("بیماری کی کتنی چھٹیاں ملتی ہیں؟");

    fireEvent.click(screen.getByRole("button", { name: /\+ New Chat/i }));
    await waitFor(() => {
      expect(createSessionMutateAsync).toHaveBeenCalledWith({ title: "New conversation" });
    });

    fireEvent.click(screen.getByLabelText("Delete conversation بیماری کی پالیسی"));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => {
      expect(deleteSessionMutateAsync).toHaveBeenCalledWith("sess-2");
    });
  });

  it("renders KPI cards and grouped topic accordion on /policy-gaps for HR Head, and blocks plain employees with zero 403s", () => {
    const sampleGaps: PolicyGapsResponse = {
      total_gaps: 4,
      groups: [
        {
          topic_tag: "parental_leave",
          total_count: 3,
          abstained_count: 2,
          downvoted_count: 1,
          sample_questions: [
            "Is adoption leave covered under company policy?",
            "How many paternity leave days apply after probation?",
          ],
          ticket_numbers: ["TKT-2026-0031"],
          latest_at: "2026-11-23T12:00:00Z",
        },
        {
          topic_tag: "remote_work",
          total_count: 1,
          abstained_count: 0,
          downvoted_count: 1,
          sample_questions: ["Does remote stipend cover electricity backup?"],
          ticket_numbers: [],
          latest_at: "2026-11-22T10:00:00Z",
        },
      ],
    };

    vi.mocked(chatHook.usePolicyGaps).mockReturnValue({
      data: sampleGaps,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.usePolicyGaps>);

    const { unmount } = render(<PolicyGapsPage />);

    expect(chatHook.usePolicyGaps).toHaveBeenCalledWith({ enabled: true });
    expect(screen.getByRole("heading", { name: "parental_leave" })).toBeInTheDocument();
    expect(screen.getAllByText("parental_leave").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Is adoption leave covered under company policy?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /TKT-2026-0031/i })).toHaveAttribute(
      "href",
      "/helpdesk"
    );

    unmount();
    vi.clearAllMocks();

    // Plain employee: must NOT call usePolicyGaps({ enabled: true }) and must show Access Restricted
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

    vi.mocked(chatHook.usePolicyGaps).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.usePolicyGaps>);

    render(<PolicyGapsPage />);

    expect(chatHook.usePolicyGaps).toHaveBeenCalledWith({ enabled: false });
    expect(screen.getByText("Access Restricted")).toBeInTheDocument();
  });

  it("shows StandbyBanner only on ApiOfflineError and renders live ApiHttpError in role=alert", () => {
    vi.mocked(chatHook.useChatSessions).mockReturnValue({
      data: [],
      isLoading: false,
      error: new ApiHttpError(422, "VALIDATION_ERROR", "Live HTTP validation error"),
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.useChatSessions>);

    const { rerender } = render(<PolicyChatPage />);
    expect(screen.getByRole("alert")).toHaveTextContent("Live HTTP validation error");
    expect(screen.queryByText(/FastAPI Backend in Standby Mode/i)).not.toBeInTheDocument();

    vi.mocked(chatHook.useChatSessions).mockReturnValue({
      data: [],
      isLoading: false,
      error: new ApiOfflineError("OFFLINE: FastAPI backend unreachable."),
      refetch: vi.fn(),
      isFetching: false,
    } as unknown as ReturnType<typeof chatHook.useChatSessions>);

    rerender(<PolicyChatPage />);
    expect(screen.getByText(/FastAPI Backend in Standby Mode/i)).toBeInTheDocument();
  });

  it("resolves header route titles cleanly for /chat, /policy-gaps, and /helpdesk", () => {
    expect(resolveRouteTitle("/chat").title).toBe("Policy Brain Chat");
    expect(resolveRouteTitle("/policy-gaps").title).toBe("HR Policy Gaps");
    expect(resolveRouteTitle("/helpdesk").title).toBe("HR & IT Helpdesk");
  });
});
