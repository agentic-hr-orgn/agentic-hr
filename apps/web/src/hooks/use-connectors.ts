"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  ConnectorResponse,
  ConnectDirectRequest,
  ReadOnlyToggleRequest,
} from "@agentic-hr/contracts";
import { useHydrated } from "@/hooks/use-hydrated";
import { ApiHttpError, apiRequest } from "@/lib/api-request";
import { getAuthHeader, isAuthQueryReady } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";
import { DEFAULT_MOCK_CONNECTOR, useConnectorStore } from "@/stores/connector-store";

export { DEFAULT_MOCK_CONNECTOR };

export interface ConnectorMutationResult<T> {
  data: T;
  source: "live" | "simulated";
}

function buildHeaders(authHeader: string | null): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  return headers;
}

export function useConnectors() {
  const hydrated = useHydrated();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  const query = useQuery<ConnectorResponse[], Error>({
    queryKey: ["connectors"],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ConnectorResponse[]>(
        `${API_BASE_URL}/api/v1/connectors`,
        { headers: buildHeaders(authHeader) },
        "Failed to load connectors"
      );

      if (res.kind === "live") {
        if (res.data && res.data.length > 0) {
          const primary =
            res.data.find((c) => c.account_email === "agentic.hr.test.ubaid.owner@gmail.com") ||
            res.data[0];
          useConnectorStore.getState().setConnector(primary);
          return [primary, ...res.data.filter((c) => c.id !== primary.id)];
        }
        return [];
      }

      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }

      // Network / offline standby fallback ONLY
      return [useConnectorStore.getState().connector];
    },
    enabled: hydrated && isAuthQueryReady(isLoaded, isSignedIn),
    staleTime: 1000 * 30,
    retry: false,
  });

  if (!hydrated) {
    return {
      ...query,
      data: undefined,
      error: null,
      isLoading: false,
      isFetching: false,
      isError: false,
      isSuccess: false,
    } as typeof query;
  }

  return query;
}

export function useToggleReadOnly() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<
    ConnectorMutationResult<ConnectorResponse>,
    Error,
    { credentialId: string; isReadOnly: boolean }
  >({
    mutationFn: async ({ credentialId, isReadOnly }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const payload: ReadOnlyToggleRequest = { is_read_only: isReadOnly };
      const res = await apiRequest<ConnectorResponse>(
        `${API_BASE_URL}/api/v1/connectors/${credentialId}/read-only`,
        {
          method: "PATCH",
          headers: buildHeaders(authHeader),
          body: JSON.stringify(payload),
        },
        "Failed to toggle Read-Only Mode"
      );

      if (res.kind === "http_error") {
        useConnectorStore.getState().setBannerMessage(null);
        useConnectorStore.getState().setErrorMessage(res.message);
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }

      const isSimulated = res.kind === "offline";
      const updated: ConnectorResponse =
        res.kind === "live"
          ? res.data
          : {
              ...useConnectorStore.getState().connector,
              id: credentialId,
              is_read_only: isReadOnly,
              updated_at: new Date().toISOString(),
            };

      const suffix = isSimulated ? " (simulated)" : "";
      const msg = updated.is_read_only
        ? `Read-Only Mode enabled. Outbound write operations (send email) are blocked.${suffix}`
        : `Read-Only Mode disabled. Outbound write operations are active.${suffix}`;

      useConnectorStore.getState().setConnector(updated);
      useConnectorStore.getState().setErrorMessage(null);
      useConnectorStore.getState().setBannerMessage(msg);
      queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [updated]);

      return { data: updated, source: isSimulated ? "simulated" : "live" };
    },
  });
}

export function useRefreshConnector() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<ConnectorMutationResult<ConnectorResponse>, Error, string>({
    mutationFn: async (credentialId: string) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ConnectorResponse>(
        `${API_BASE_URL}/api/v1/connectors/${credentialId}/refresh`,
        {
          method: "POST",
          headers: buildHeaders(authHeader),
        },
        "Failed to refresh OAuth token"
      );

      if (res.kind === "http_error") {
        useConnectorStore.getState().setBannerMessage(null);
        useConnectorStore.getState().setErrorMessage(res.message);
        if (res.status === 401 || res.message.includes("CONNECTOR_AUTH_EXPIRED")) {
          // Reflect tripped circuit breaker immediately and refresh server state
          const current = useConnectorStore.getState().connector;
          const tripped: ConnectorResponse = {
            ...current,
            id: credentialId,
            status: "needs_reconnect",
            jobs_paused: true,
            error_message: res.message,
            updated_at: new Date().toISOString(),
          };
          useConnectorStore.getState().setConnector(tripped);
          queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [tripped]);
          queryClient.invalidateQueries({ queryKey: ["connectors"] });
        }
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }

      const isSimulated = res.kind === "offline";
      const updated: ConnectorResponse =
        res.kind === "live"
          ? res.data
          : {
              ...useConnectorStore.getState().connector,
              id: credentialId,
              status: "connected",
              jobs_paused: false,
              error_message: null,
              token_expiry: new Date(Date.now() + 3600 * 1000).toISOString(),
              updated_at: new Date().toISOString(),
            };

      const suffix = isSimulated ? " (simulated)" : "";
      useConnectorStore.getState().setConnector(updated);
      useConnectorStore.getState().setErrorMessage(null);
      useConnectorStore
        .getState()
        .setBannerMessage(`OAuth access token refreshed successfully (+1h expiry).${suffix}`);
      queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [updated]);

      return { data: updated, source: isSimulated ? "simulated" : "live" };
    },
  });
}

export function useRevokeConnector() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<ConnectorMutationResult<{ status: string; id: string }>, Error, string>({
    mutationFn: async (credentialId: string) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<{ status: string; id: string }>(
        `${API_BASE_URL}/api/v1/connectors/${credentialId}`,
        {
          method: "DELETE",
          headers: buildHeaders(authHeader),
        },
        "Failed to disconnect connector"
      );

      if (res.kind === "http_error") {
        useConnectorStore.getState().setBannerMessage(null);
        useConnectorStore.getState().setErrorMessage(res.message);
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }

      const isSimulated = res.kind === "offline";
      const current = useConnectorStore.getState().connector;
      const updated: ConnectorResponse = {
        ...current,
        id: credentialId,
        status: "revoked",
        jobs_paused: true,
        updated_at: new Date().toISOString(),
      };

      const suffix = isSimulated ? " (simulated)" : "";
      useConnectorStore.getState().setConnector(updated);
      useConnectorStore.getState().setErrorMessage(null);
      useConnectorStore
        .getState()
        .setBannerMessage(
          `Google Workspace connector disconnected. Status set to Revoked.${suffix}`
        );
      queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [updated]);

      return {
        data: { status: "revoked", id: credentialId },
        source: isSimulated ? "simulated" : "live",
      };
    },
  });
}

export function useReconnectConnector() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<ConnectorMutationResult<ConnectorResponse>, Error, string>({
    mutationFn: async (credentialId: string) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ConnectorResponse>(
        `${API_BASE_URL}/api/v1/connectors/${credentialId}/reconnect`,
        {
          method: "POST",
          headers: buildHeaders(authHeader),
        },
        "Failed to reconnect connector"
      );

      if (res.kind === "http_error") {
        // Live mode 409 OAUTH_REAUTH_REQUIRED -> redirect browser to fresh Google OAuth consent
        const authUrl =
          typeof res.body?.authorization_url === "string"
            ? res.body.authorization_url
            : typeof (res.body?.error as Record<string, unknown> | undefined)?.authorization_url ===
                "string"
              ? String((res.body?.error as Record<string, unknown>).authorization_url)
              : null;

        if (res.status === 409 && res.code === "OAUTH_REAUTH_REQUIRED" && authUrl) {
          if (typeof window !== "undefined") {
            window.location.assign(authUrl);
          }
          throw new ApiHttpError(res.status, res.code, res.message, res.body);
        }

        useConnectorStore.getState().setBannerMessage(null);
        useConnectorStore.getState().setErrorMessage(res.message);
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }

      const isSimulated = res.kind === "offline";
      const updated: ConnectorResponse =
        res.kind === "live"
          ? res.data
          : {
              ...useConnectorStore.getState().connector,
              id: credentialId,
              status: "connected",
              jobs_paused: false,
              error_message: null,
              token_expiry: new Date(Date.now() + 3600 * 1000).toISOString(),
              updated_at: new Date().toISOString(),
            };

      const suffix = isSimulated ? " (simulated)" : "";
      useConnectorStore.getState().setConnector(updated);
      useConnectorStore.getState().setErrorMessage(null);
      useConnectorStore
        .getState()
        .setBannerMessage(`Google Workspace connector reconnected successfully.${suffix}`);
      queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [updated]);

      return { data: updated, source: isSimulated ? "simulated" : "live" };
    },
  });
}

export function useGoogleAuthorize() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<{ authorization_url: string }, Error, void>({
    mutationFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<{ authorization_url: string }>(
        `${API_BASE_URL}/api/v1/connectors/google/oauth/authorize`,
        { headers: buildHeaders(authHeader) },
        "Failed to get authorization URL"
      );

      if (res.kind === "live") {
        return res.data;
      }
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("FastAPI backend is offline; cannot start OAuth authorization.");
    },
  });
}

export function useConnectDirect() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<ConnectorResponse, Error, ConnectDirectRequest>({
    mutationFn: async (payload: ConnectDirectRequest) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ConnectorResponse>(
        `${API_BASE_URL}/api/v1/connectors/google/connect`,
        {
          method: "POST",
          headers: buildHeaders(authHeader),
          body: JSON.stringify(payload),
        },
        "Failed to connect credentials"
      );

      if (res.kind === "live") {
        return res.data;
      }
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("FastAPI backend is offline; cannot connect credentials.");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
  });
}
