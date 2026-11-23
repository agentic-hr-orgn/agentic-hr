"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiStreamChat,
  askHrFromMessage,
  createChatSession,
  deleteChatSession,
  getChatSessionMessages,
  listChatSessions,
  listPolicyGaps,
  sendChatMessage,
  submitAnswerFeedback,
  type AnswerFeedbackRequest,
  type AnswerFeedbackResponse,
  type AskHrRequest,
  type AskHrResponse,
  type ChatMessage,
  type ChatSession,
  type ChatSessionCreateRequest,
  type ChatTurnRequest,
  type ChatTurnResponse,
  type PolicyBrainStreamDonePayload,
  type PolicyBrainStreamEvent,
  type PolicyGapsResponse,
  type Stage7ApiResult,
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

export function useChatSessions(options?: { enabled?: boolean }) {
  const hydrated = useHydrated();
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const enabled = options?.enabled ?? true;

  return useQuery<ChatSession[], Error>({
    queryKey: ["chat", "sessions", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await listChatSessions({ baseUrl: API_BASE_URL, authHeader });
      return unwrapStage7Result(res);
    },
    enabled: hydrated && isAuthQueryReady(isLoaded, isSignedIn) && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useChatMessages(
  sessionId: string | null | undefined,
  options?: { enabled?: boolean }
) {
  const hydrated = useHydrated();
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const enabled = options?.enabled ?? true;

  return useQuery<ChatMessage[], Error>({
    queryKey: ["chat", "messages", orgId, sessionId],
    queryFn: async () => {
      if (!sessionId) return [];
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await getChatSessionMessages(sessionId, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(res);
    },
    enabled: hydrated && isAuthQueryReady(isLoaded, isSignedIn) && Boolean(sessionId) && enabled,
    staleTime: 1000 * 15,
    retry: false,
  });
}

export function useCreateChatSession() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<ChatSession, Error, ChatSessionCreateRequest | undefined>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await createChatSession(payload ?? {}, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; chat session could not be created."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["chat", "sessions"] });
    },
  });
}

export function useDeleteChatSession() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<null, Error, string>({
    mutationFn: async (sessionId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await deleteChatSession(sessionId, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; chat session could not be deleted."
      );
    },
    onSuccess: (_data, sessionId) => {
      queryClient.invalidateQueries({ queryKey: ["chat", "sessions"] });
      queryClient.removeQueries({ queryKey: ["chat", "messages", undefined, sessionId] });
      queryClient.invalidateQueries({ queryKey: ["chat", "messages"] });
    },
  });
}

export function useSendChatMessage() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<ChatTurnResponse, Error, { sessionId: string; payload: ChatTurnRequest }>({
    mutationFn: async ({ sessionId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await sendChatMessage(sessionId, payload, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; could not reach Policy Brain."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["chat", "messages"] });
      queryClient.invalidateQueries({ queryKey: ["chat", "sessions"] });
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
      queryClient.invalidateQueries({ queryKey: ["chat", "policy-gaps"] });
    },
  });
}

export interface StreamChatParams {
  sessionId: string;
  payload: ChatTurnRequest;
  onEvent?: (event: PolicyBrainStreamEvent) => void;
}

/**
 * Auth-wired Policy Brain SSE streaming hook wrapping `apiStreamChat` with
 * `API_BASE_URL`, Clerk `getAuthHeader`, `AbortController`, clean live/offline errors,
 * and automatic React Query cache invalidation upon `done`.
 */
export function useStreamChat() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [isStreaming, setIsStreaming] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const abort = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  useEffect(() => {
    return () => {
      controllerRef.current?.abort();
      controllerRef.current = null;
    };
  }, []);

  const streamChat = useCallback(
    async ({
      sessionId,
      payload,
      onEvent,
    }: StreamChatParams): Promise<{
      aborted: boolean;
      donePayload: PolicyBrainStreamDonePayload | null;
    }> => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      setIsStreaming(true);

      try {
        const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
        const result = await apiStreamChat(sessionId, payload, {
          baseUrl: API_BASE_URL,
          authHeader,
          signal: controller.signal,
          onEvent,
        });

        if (result.kind === "aborted") {
          return { aborted: true, donePayload: result.data };
        }
        if (result.kind === "http_error") {
          throw new ApiHttpError(result.status, result.code, result.message, result.body);
        }
        if (result.kind === "offline") {
          throw new ApiOfflineError(
            "OFFLINE: FastAPI backend is offline; streaming chat is unavailable."
          );
        }

        queryClient.invalidateQueries({ queryKey: ["chat", "messages"] });
        queryClient.invalidateQueries({ queryKey: ["chat", "sessions"] });
        if (result.data?.ticket_id) {
          queryClient.invalidateQueries({ queryKey: ["tickets"] });
          queryClient.invalidateQueries({ queryKey: ["chat", "policy-gaps"] });
        }
        return { aborted: false, donePayload: result.data };
      } finally {
        if (controllerRef.current === controller) {
          controllerRef.current = null;
        }
        setIsStreaming(false);
      }
    },
    [getToken, isLoaded, isSignedIn, queryClient]
  );

  return {
    streamChat,
    isStreaming,
    abort,
  };
}

export function useSubmitAnswerFeedback() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    AnswerFeedbackResponse,
    Error,
    { messageId: string; payload: AnswerFeedbackRequest }
  >({
    mutationFn: async ({ messageId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await submitAnswerFeedback(messageId, payload, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; feedback could not be recorded."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["chat", "policy-gaps"] });
    },
  });
}

export function useAskHrFromMessage() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<AskHrResponse, Error, { messageId: string; payload?: AskHrRequest }>({
    mutationFn: async ({ messageId, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await askHrFromMessage(messageId, payload ?? {}, {
        baseUrl: API_BASE_URL,
        authHeader,
      });
      return unwrapStage7Result(
        res,
        "OFFLINE: FastAPI backend is offline; HR escalation ticket could not be created."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["chat", "messages"] });
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
      queryClient.invalidateQueries({ queryKey: ["chat", "policy-gaps"] });
    },
  });
}

/**
 * Staff / HR-only policy knowledge gaps hook (`GET /api/v1/chat/policy-gaps`).
 * Defaults to `enabled: false` so plain Employees never trigger background 403 requests.
 */
export function usePolicyGaps(options?: { enabled?: boolean }) {
  const hydrated = useHydrated();
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const enabled = options?.enabled ?? false;

  return useQuery<PolicyGapsResponse, Error>({
    queryKey: ["chat", "policy-gaps", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await listPolicyGaps({ baseUrl: API_BASE_URL, authHeader });
      return unwrapStage7Result(res);
    },
    enabled: hydrated && isAuthQueryReady(isLoaded, isSignedIn) && enabled,
    staleTime: 1000 * 30,
    retry: false,
  });
}
