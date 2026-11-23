"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addTicketMessage,
  askHelpdesk,
  createTicket,
  getTicketDetail,
  listMyTickets,
  listStaffTicketQueue,
  listTicketRules,
  rateTicket,
  reopenTicket,
  updateTicket,
  updateTicketRule,
  type HelpdeskAskRequest,
  type HelpdeskAskResponse,
  type Stage7ApiResult,
  type Ticket,
  type TicketAssignmentRule,
  type TicketCategory,
  type TicketCreateRequest,
  type TicketDetail,
  type TicketMessage,
  type TicketMessageCreateRequest,
  type TicketPriority,
  type TicketRateRequest,
  type TicketReopenRequest,
  type TicketRuleUpdateRequest,
  type TicketStatus,
  type TicketUpdateRequest,
} from "@agentic-hr/contracts";
import { useHydrated } from "@/hooks/use-hydrated";
import { ApiHttpError, ApiOfflineError } from "@/lib/api-request";
import { getAuthHeader, isAuthQueryReady } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

function unwrapStage7Result<T>(
  res: Stage7ApiResult<T>,
  offlineMessage = "OFFLINE: FastAPI backend is unreachable."
): T {
  if (res.kind === "live") return res.data;
  if (res.kind === "http_error") {
    throw new ApiHttpError(res.status, res.code, res.message, res.body);
  }
  throw new ApiOfflineError(offlineMessage);
}

export function useMyTickets(filters?: { status?: TicketStatus }, options?: { enabled?: boolean }) {
  const hydrated = useHydrated();
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const enabled = options?.enabled ?? true;

  return useQuery<Ticket[], Error>({
    queryKey: ["tickets", "me", orgId, filters?.status ?? null],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await listMyTickets(filters, { baseUrl: API_BASE_URL, authHeader });
      return unwrapStage7Result(res);
    },
    enabled: hydrated && isAuthQueryReady(isLoaded, isSignedIn) && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

/**
 * Helpdesk staff queue hook (`GET /api/v1/tickets/queue`).
 * Defaults to `enabled: false` so plain Employees trigger zero background 403 requests.
 */
export function useStaffTicketQueue(
  filters?: {
    status?: TicketStatus;
    category?: TicketCategory;
    priority?: TicketPriority;
    assigned_to_user_id?: string;
  },
  options?: { enabled?: boolean }
) {
  const hydrated = useHydrated();
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const enabled = options?.enabled ?? false;

  return useQuery<Ticket[], Error>({
    queryKey: ["tickets", "queue", orgId, filters ?? null],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await listStaffTicketQueue(filters, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(res);
    },
    enabled: hydrated && isAuthQueryReady(isLoaded, isSignedIn) && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useTicketDetail(
  ticketId: string | null | undefined,
  options?: { enabled?: boolean }
) {
  const hydrated = useHydrated();
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const enabled = options?.enabled ?? true;

  return useQuery<TicketDetail, Error>({
    queryKey: ["tickets", "detail", orgId, ticketId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await getTicketDetail(ticketId as string, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(res);
    },
    enabled: hydrated && isAuthQueryReady(isLoaded, isSignedIn) && Boolean(ticketId) && enabled,
    staleTime: 1000 * 15,
    retry: false,
  });
}

export function useCreateTicket() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<Ticket, Error, TicketCreateRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await createTicket(payload, { baseUrl: API_BASE_URL, authHeader });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; ticket could not be created."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
}

export function useUpdateTicket() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<Ticket, Error, { ticketId: string; payload: TicketUpdateRequest }>({
    mutationFn: async ({ ticketId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await updateTicket(ticketId, payload, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; ticket could not be updated."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
}

export function useAddTicketMessage() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    TicketMessage,
    Error,
    { ticketId: string; payload: TicketMessageCreateRequest }
  >({
    mutationFn: async ({ ticketId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await addTicketMessage(ticketId, payload, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; reply could not be posted."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
}

export function useRateTicket() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<Ticket, Error, { ticketId: string; payload: TicketRateRequest }>({
    mutationFn: async ({ ticketId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await rateTicket(ticketId, payload, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; rating could not be submitted."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
}

export function useReopenTicket() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<Ticket, Error, { ticketId: string; payload: TicketReopenRequest }>({
    mutationFn: async ({ ticketId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await reopenTicket(ticketId, payload, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; ticket could not be reopened."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
  });
}

/**
 * Helpdesk assignment & SLA rules hook (`GET /api/v1/tickets/rules`).
 * Defaults to `enabled: false` so plain Employees/Managers without HR Head access trigger zero 403s.
 */
export function useTicketRules(options?: { enabled?: boolean }) {
  const hydrated = useHydrated();
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const enabled = options?.enabled ?? false;

  return useQuery<TicketAssignmentRule[], Error>({
    queryKey: ["tickets", "rules", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await listTicketRules({ baseUrl: API_BASE_URL, authHeader });
      return unwrapStage7Result(res);
    },
    enabled: hydrated && isAuthQueryReady(isLoaded, isSignedIn) && enabled,
    staleTime: 1000 * 60,
    retry: false,
  });
}

export function useUpdateTicketRule() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    TicketAssignmentRule,
    Error,
    { category: TicketCategory; payload: TicketRuleUpdateRequest }
  >({
    mutationFn: async ({ category, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await updateTicketRule(category, payload, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; routing rule could not be saved."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets", "rules"] });
    },
  });
}

export function useAskHelpdesk() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<HelpdeskAskResponse, Error, HelpdeskAskRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await askHelpdesk(payload, { baseUrl: API_BASE_URL, authHeader });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; Helpdesk assistant is unreachable."
      );
    },
    onSuccess: (data) => {
      if (data.ticket) {
        queryClient.invalidateQueries({ queryKey: ["tickets"] });
      }
    },
  });
}
