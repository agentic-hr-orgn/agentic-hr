import createClient, { type Client } from "openapi-fetch";
import type { paths } from "./api-client";
import type {
  AnswerFeedbackRequest,
  AnswerFeedbackResponse,
  AskHrRequest,
  AskHrResponse,
  ChatMessage,
  ChatSession,
  ChatSessionCreateRequest,
  ChatTurnRequest,
  ChatTurnResponse,
  HelpdeskAskRequest,
  HelpdeskAskResponse,
  PolicyBrainStreamDonePayload,
  PolicyBrainStreamEvent,
  PolicyGapsResponse,
  Ticket,
  TicketAssignmentRule,
  TicketCategory,
  TicketCreateRequest,
  TicketDetail,
  TicketMessage,
  TicketMessageCreateRequest,
  TicketPriority,
  TicketRateRequest,
  TicketReopenRequest,
  TicketRuleUpdateRequest,
  TicketStatus,
  TicketSummaryItem,
  TicketUpdateRequest,
} from "./index";

function resolveApiBaseUrl(baseUrl?: string): string {
  const url =
    baseUrl ||
    (typeof process !== "undefined" && process.env?.NEXT_PUBLIC_API_URL) ||
    "http://localhost:8000";
  return url.replace(/\/+$/, "");
}

/**
 * Creates a typed API client configured against the Agentic HR backend.
 * Uses NEXT_PUBLIC_API_URL or defaults to http://localhost:8000.
 */
export function createApiClient(baseUrl?: string): Client<paths> {
  return createClient<paths>({ baseUrl: resolveApiBaseUrl(baseUrl) });
}

/**
 * Pre-configured default API client instance for immediate consumption.
 */
export const apiClient: Client<paths> = createApiClient();

// ============================================================================
// Stage 7 Typed REST & SSE Client Layer
// ============================================================================

export interface Stage7RequestOptions {
  baseUrl?: string;
  authHeader?: string | null;
  signal?: AbortSignal;
}

export type Stage7ApiResult<T> =
  | { kind: "live"; status: number; data: T }
  | {
      kind: "http_error";
      status: number;
      code: string;
      message: string;
      body: Record<string, unknown> | null;
    }
  | { kind: "offline"; error: unknown };

export type StreamChatResult =
  | { kind: "live"; status: number; data: PolicyBrainStreamDonePayload | null }
  | { kind: "aborted"; data: PolicyBrainStreamDonePayload | null }
  | {
      kind: "http_error";
      status: number;
      code: string;
      message: string;
      body: Record<string, unknown> | null;
    }
  | { kind: "offline"; error: unknown };

function buildStage7Headers(
  authHeader?: string | null,
  extra?: Record<string, string>
): Record<string, string> {
  const headers: Record<string, string> = { ...(extra ?? {}) };
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  return headers;
}

function parseStage7ErrorEnvelope(
  body: Record<string, unknown> | null,
  status: number,
  fallbackPrefix: string
): { code: string; message: string } {
  const errObj =
    body && typeof body.error === "object" && body.error !== null
      ? (body.error as Record<string, unknown>)
      : null;

  const code =
    (typeof errObj?.code === "string" && errObj.code) ||
    (typeof body?.code === "string" && body.code) ||
    `HTTP_${status}`;

  const message =
    (typeof errObj?.message === "string" && errObj.message) ||
    (typeof body?.detail === "string" && body.detail) ||
    (typeof body?.message === "string" && body.message) ||
    `${fallbackPrefix} (HTTP ${status})`;

  return { code, message };
}

function isAbortError(err: unknown): boolean {
  return err instanceof Error && err.name === "AbortError";
}

async function stage7Request<T>(
  path: string,
  init: RequestInit,
  options: Stage7RequestOptions | undefined,
  fallbackPrefix: string
): Promise<Stage7ApiResult<T>> {
  const url = `${resolveApiBaseUrl(options?.baseUrl)}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      signal: options?.signal ?? init.signal,
      headers: buildStage7Headers(
        options?.authHeader,
        init.body ? { "Content-Type": "application/json" } : undefined
      ),
    });
  } catch (err: unknown) {
    return { kind: "offline", error: err };
  }

  if (res.status === 204) {
    return { kind: "live", status: res.status, data: null as T };
  }

  let parsedBody: unknown = null;
  try {
    if (typeof res.json === "function") {
      parsedBody = await res.json();
    }
  } catch {
    parsedBody = null;
  }

  if (res.ok) {
    return { kind: "live", status: res.status, data: parsedBody as T };
  }

  const bodyObj =
    parsedBody && typeof parsedBody === "object" ? (parsedBody as Record<string, unknown>) : null;
  const { code, message } = parseStage7ErrorEnvelope(bodyObj, res.status, fallbackPrefix);
  return {
    kind: "http_error",
    status: res.status,
    code,
    message,
    body: bodyObj,
  };
}

function parseSseFrame(rawFrame: string): PolicyBrainStreamEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];

  for (const line of rawFrame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (dataLines.length === 0) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }

  if (eventName === "status") {
    return { type: "status", data: parsed as { node: string } };
  }
  if (eventName === "token") {
    return { type: "token", data: parsed as { text: string } };
  }
  if (eventName === "done") {
    return { type: "done", data: parsed as PolicyBrainStreamDonePayload };
  }
  if (eventName === "error") {
    return { type: "error", data: parsed as { detail: string } };
  }
  return null;
}

/**
 * Streams Policy Brain SSE events (`status`, `token`, `done`, `error`) using fetch +
 * ReadableStream with clean discrimination between offline failures, HTTP errors, and abort.
 */
export async function apiStreamChat(
  sessionId: string,
  payload: ChatTurnRequest,
  options?: Stage7RequestOptions & {
    onEvent?: (event: PolicyBrainStreamEvent) => void;
  }
): Promise<StreamChatResult> {
  const url = `${resolveApiBaseUrl(options?.baseUrl)}/api/v1/chat/sessions/${encodeURIComponent(
    sessionId
  )}/stream`;

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      signal: options?.signal,
      headers: buildStage7Headers(options?.authHeader, {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      body: JSON.stringify(payload),
    });
  } catch (err: unknown) {
    if (isAbortError(err)) {
      return { kind: "aborted", data: null };
    }
    return { kind: "offline", error: err };
  }

  if (!res.ok) {
    let parsedBody: unknown = null;
    try {
      if (typeof res.json === "function") {
        parsedBody = await res.json();
      }
    } catch {
      parsedBody = null;
    }
    const bodyObj =
      parsedBody && typeof parsedBody === "object" ? (parsedBody as Record<string, unknown>) : null;
    const { code, message } = parseStage7ErrorEnvelope(
      bodyObj,
      res.status,
      "Failed to stream chat response"
    );
    return { kind: "http_error", status: res.status, code, message, body: bodyObj };
  }

  if (!res.body) {
    return { kind: "live", status: res.status, data: null };
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let donePayload: PolicyBrainStreamDonePayload | null = null;

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        if (!part.trim()) continue;
        const ev = parseSseFrame(part);
        if (!ev) continue;
        if (ev.type === "done") {
          donePayload = ev.data;
        }
        options?.onEvent?.(ev);
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) {
      for (const part of buffer.split(/\r?\n\r?\n/)) {
        if (!part.trim()) continue;
        const ev = parseSseFrame(part);
        if (!ev) continue;
        if (ev.type === "done") {
          donePayload = ev.data;
        }
        options?.onEvent?.(ev);
      }
    }
    return { kind: "live", status: res.status, data: donePayload };
  } catch (err: unknown) {
    if (isAbortError(err)) {
      return { kind: "aborted", data: donePayload };
    }
    return { kind: "offline", error: err };
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // reader already released
    }
  }
}

// ============================================================================
// Chat & Policy Gaps REST Client Functions
// ============================================================================

export function createChatSession(
  payload: ChatSessionCreateRequest = {},
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<ChatSession>> {
  return stage7Request<ChatSession>(
    "/api/v1/chat/sessions",
    { method: "POST", body: JSON.stringify(payload) },
    options,
    "Failed to create chat session"
  );
}

export function listChatSessions(
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<ChatSession[]>> {
  return stage7Request<ChatSession[]>(
    "/api/v1/chat/sessions",
    { method: "GET" },
    options,
    "Failed to list chat sessions"
  );
}

export function getChatSessionMessages(
  sessionId: string,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<ChatMessage[]>> {
  return stage7Request<ChatMessage[]>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
    { method: "GET" },
    options,
    "Failed to load chat session messages"
  );
}

export function deleteChatSession(
  sessionId: string,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<null>> {
  return stage7Request<null>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
    options,
    "Failed to delete chat session"
  );
}

export function sendChatMessage(
  sessionId: string,
  payload: ChatTurnRequest,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<ChatTurnResponse>> {
  return stage7Request<ChatTurnResponse>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
    { method: "POST", body: JSON.stringify(payload) },
    options,
    "Failed to send chat message"
  );
}

export function submitAnswerFeedback(
  messageId: string,
  payload: AnswerFeedbackRequest,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<AnswerFeedbackResponse>> {
  return stage7Request<AnswerFeedbackResponse>(
    `/api/v1/chat/messages/${encodeURIComponent(messageId)}/feedback`,
    { method: "POST", body: JSON.stringify(payload) },
    options,
    "Failed to submit answer feedback"
  );
}

export function askHrFromMessage(
  messageId: string,
  payload: AskHrRequest = {},
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<AskHrResponse>> {
  return stage7Request<AskHrResponse>(
    `/api/v1/chat/messages/${encodeURIComponent(messageId)}/ask-hr`,
    { method: "POST", body: JSON.stringify(payload) },
    options,
    "Failed to escalate message to HR"
  );
}

export function listPolicyGaps(
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<PolicyGapsResponse>> {
  return stage7Request<PolicyGapsResponse>(
    "/api/v1/chat/policy-gaps",
    { method: "GET" },
    options,
    "Failed to load policy knowledge gaps"
  );
}

// ============================================================================
// Helpdesk Tickets & Read-Only Agent REST Client Functions
// ============================================================================

export function createTicket(
  payload: TicketCreateRequest,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<Ticket>> {
  return stage7Request<Ticket>(
    "/api/v1/tickets",
    { method: "POST", body: JSON.stringify(payload) },
    options,
    "Failed to create ticket"
  );
}

export function listMyTickets(
  filters?: { status?: TicketStatus },
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<Ticket[]>> {
  const qs = filters?.status ? `?status=${encodeURIComponent(filters.status)}` : "";
  return stage7Request<Ticket[]>(
    `/api/v1/tickets/me${qs}`,
    { method: "GET" },
    options,
    "Failed to list your tickets"
  );
}

export function listStaffTicketQueue(
  filters?: {
    status?: TicketStatus;
    category?: TicketCategory;
    priority?: TicketPriority;
    assigned_to_user_id?: string;
  },
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<Ticket[]>> {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.category) params.set("category", filters.category);
  if (filters?.priority) params.set("priority", filters.priority);
  if (filters?.assigned_to_user_id) params.set("assigned_to_user_id", filters.assigned_to_user_id);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return stage7Request<Ticket[]>(
    `/api/v1/tickets/queue${qs}`,
    { method: "GET" },
    options,
    "Failed to load staff ticket queue"
  );
}

export function listTicketSummary(
  filters?: {
    status?: TicketStatus;
    category?: TicketCategory;
  },
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<TicketSummaryItem[]>> {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.category) params.set("category", filters.category);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return stage7Request<TicketSummaryItem[]>(
    `/api/v1/tickets/summary${qs}`,
    { method: "GET" },
    options,
    "Failed to load ticket summary"
  );
}

export function getTicketDetail(
  ticketId: string,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<TicketDetail>> {
  return stage7Request<TicketDetail>(
    `/api/v1/tickets/${encodeURIComponent(ticketId)}`,
    { method: "GET" },
    options,
    "Failed to load ticket details"
  );
}

export function updateTicket(
  ticketId: string,
  payload: TicketUpdateRequest,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<Ticket>> {
  return stage7Request<Ticket>(
    `/api/v1/tickets/${encodeURIComponent(ticketId)}`,
    { method: "PATCH", body: JSON.stringify(payload) },
    options,
    "Failed to update ticket"
  );
}

export function addTicketMessage(
  ticketId: string,
  payload: TicketMessageCreateRequest,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<TicketMessage>> {
  return stage7Request<TicketMessage>(
    `/api/v1/tickets/${encodeURIComponent(ticketId)}/messages`,
    { method: "POST", body: JSON.stringify(payload) },
    options,
    "Failed to post ticket message"
  );
}

export function rateTicket(
  ticketId: string,
  payload: TicketRateRequest,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<Ticket>> {
  return stage7Request<Ticket>(
    `/api/v1/tickets/${encodeURIComponent(ticketId)}/rate`,
    { method: "POST", body: JSON.stringify(payload) },
    options,
    "Failed to submit ticket rating"
  );
}

export function reopenTicket(
  ticketId: string,
  payload: TicketReopenRequest,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<Ticket>> {
  return stage7Request<Ticket>(
    `/api/v1/tickets/${encodeURIComponent(ticketId)}/reopen`,
    { method: "POST", body: JSON.stringify(payload) },
    options,
    "Failed to reopen ticket"
  );
}

export function listTicketRules(
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<TicketAssignmentRule[]>> {
  return stage7Request<TicketAssignmentRule[]>(
    "/api/v1/tickets/rules",
    { method: "GET" },
    options,
    "Failed to list ticket assignment rules"
  );
}

export function updateTicketRule(
  category: TicketCategory,
  payload: TicketRuleUpdateRequest,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<TicketAssignmentRule>> {
  return stage7Request<TicketAssignmentRule>(
    `/api/v1/tickets/rules/${encodeURIComponent(category)}`,
    { method: "PUT", body: JSON.stringify(payload) },
    options,
    "Failed to update ticket assignment rule"
  );
}

export function askHelpdesk(
  payload: HelpdeskAskRequest,
  options?: Stage7RequestOptions
): Promise<Stage7ApiResult<HelpdeskAskResponse>> {
  return stage7Request<HelpdeskAskResponse>(
    "/api/v1/helpdesk/ask",
    { method: "POST", body: JSON.stringify(payload) },
    options,
    "Failed to ask Helpdesk assistant"
  );
}
