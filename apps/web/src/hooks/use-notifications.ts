"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  ExpiryScanSummary,
  MarkAllReadResponse,
  NotificationItem,
  NotificationListResponse,
  TriggerExpiryScanRequest,
} from "@agentic-hr/contracts";
import { ApiHttpError, ApiOfflineError, apiRequest } from "@/lib/api-request";
import { getAuthHeader } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

export interface NotificationListParams {
  unreadOnly?: boolean;
  category?: string;
  limit?: number;
  offset?: number;
}

function buildJsonHeaders(authHeader: string | null): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  return headers;
}

export function useNotifications(params: NotificationListParams = {}) {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();
  const { unreadOnly = false, category = "", limit = 20, offset = 0 } = params;

  return useQuery<NotificationListResponse, Error>({
    queryKey: ["notifications", orgId, { unreadOnly, category, limit, offset }],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const url = new URL(`${API_BASE_URL}/api/v1/notifications`);
      if (unreadOnly) url.searchParams.set("unread_only", "true");
      if (category) url.searchParams.set("category", category);
      url.searchParams.set("limit", String(limit));
      url.searchParams.set("offset", String(offset));

      const res = await apiRequest<NotificationListResponse>(
        url.toString(),
        { headers: buildJsonHeaders(authHeader) },
        "Failed to load notifications"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is unreachable.");
    },
    enabled: isLoaded,
    staleTime: 1000 * 30,
    retry: false,
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<NotificationItem, Error, string>({
    mutationFn: async (notificationId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<NotificationItem>(
        `${API_BASE_URL}/api/v1/notifications/${notificationId}/read`,
        {
          method: "PATCH",
          headers: buildJsonHeaders(authHeader),
        },
        "Failed to mark notification as read"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; notification status was not updated."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useMarkAllNotificationsRead() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<MarkAllReadResponse, Error, void>({
    mutationFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<MarkAllReadResponse>(
        `${API_BASE_URL}/api/v1/notifications/read-all`,
        {
          method: "POST",
          headers: buildJsonHeaders(authHeader),
        },
        "Failed to mark all notifications read"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; notifications could not be marked read."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useTriggerExpiryScan() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<ExpiryScanSummary, Error, TriggerExpiryScanRequest | undefined>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ExpiryScanSummary>(
        `${API_BASE_URL}/api/v1/notifications/trigger-expiry-scan`,
        {
          method: "POST",
          headers: buildJsonHeaders(authHeader),
          body: JSON.stringify(payload ?? {}),
        },
        "Failed to run document expiry scan"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is offline; expiry scan cannot execute.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}
