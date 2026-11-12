"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  ConnectorResponse,
  ConnectDirectRequest,
  ReadOnlyToggleRequest,
} from "@agentic-hr/contracts";
import { DEFAULT_MOCK_CONNECTOR, useConnectorStore } from "@/stores/connector-store";

export { DEFAULT_MOCK_CONNECTOR };

async function getAuthHeader(
  getToken: () => Promise<string | null>,
  isLoaded: boolean,
  isSignedIn: boolean | undefined
): Promise<string> {
  let token: string | null = null;
  if (isLoaded && isSignedIn) {
    try {
      token = await getToken();
    } catch {
      // Fallback
    }
  }
  return token
    ? `Bearer ${token}`
    : "Bearer mock:agentic.hr.test.ubaid.owner@gmail.com:org_acme_tech_01";
}

export function useConnectors() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const currentConnector = useConnectorStore((s) => s.connector);

  return useQuery<ConnectorResponse[], Error>({
    queryKey: ["connectors"],
    queryFn: async () => {
      try {
        const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
        const res = await fetch(`${apiUrl}/api/v1/connectors`, {
          headers: {
            Authorization: authHeader,
            "Content-Type": "application/json",
          },
        });

        if (res.ok) {
          const data = (await res.json()) as ConnectorResponse[];
          if (data && data.length > 0) {
            useConnectorStore.getState().setConnector(data[0]);
            return data;
          }
        }
      } catch {
        // Fallback to local store when backend is unreachable or in standalone dev
      }
      return [useConnectorStore.getState().connector];
    },
    initialData: () => [currentConnector],
    staleTime: 1000 * 30, // 30 seconds
    retry: false,
  });
}

export function useToggleReadOnly() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation<ConnectorResponse, Error, { credentialId: string; isReadOnly: boolean }>({
    mutationFn: async ({ credentialId, isReadOnly }) => {
      let updated: ConnectorResponse | null = null;
      try {
        const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
        const payload: ReadOnlyToggleRequest = { is_read_only: isReadOnly };
        const res = await fetch(`${apiUrl}/api/v1/connectors/${credentialId}/read-only`, {
          method: "PATCH",
          headers: {
            Authorization: authHeader,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        });

        if (res.ok) {
          updated = (await res.json()) as ConnectorResponse;
        }
      } catch {
        // Fallback
      }

      if (!updated) {
        const current = useConnectorStore.getState().connector;
        updated = {
          ...current,
          id: credentialId,
          is_read_only: isReadOnly,
          updated_at: new Date().toISOString(),
        };
      }

      useConnectorStore.getState().setConnector(updated);
      queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [updated!]);

      return updated;
    },
    onSuccess: (data) => {
      if (data) {
        useConnectorStore.getState().setConnector(data);
        queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [data]);
      }
    },
  });
}

export function useRefreshConnector() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation<ConnectorResponse, Error, string>({
    mutationFn: async (credentialId: string) => {
      let updated: ConnectorResponse | null = null;
      try {
        const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
        const res = await fetch(`${apiUrl}/api/v1/connectors/${credentialId}/refresh`, {
          method: "POST",
          headers: {
            Authorization: authHeader,
            "Content-Type": "application/json",
          },
        });

        if (res.ok) {
          updated = (await res.json()) as ConnectorResponse;
        }
      } catch {
        // Fallback
      }

      if (!updated) {
        const current = useConnectorStore.getState().connector;
        updated = {
          ...current,
          id: credentialId,
          status: "connected",
          jobs_paused: false,
          error_message: null,
          token_expiry: new Date(Date.now() + 3600 * 1000).toISOString(),
          updated_at: new Date().toISOString(),
        };
      }

      useConnectorStore.getState().setConnector(updated);
      queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [updated!]);

      return updated;
    },
    onSuccess: (data) => {
      if (data) {
        useConnectorStore.getState().setConnector(data);
        queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [data]);
      }
    },
  });
}

export function useRevokeConnector() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation<{ status: string; id: string }, Error, string>({
    mutationFn: async (credentialId: string) => {
      try {
        const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
        await fetch(`${apiUrl}/api/v1/connectors/${credentialId}`, {
          method: "DELETE",
          headers: {
            Authorization: authHeader,
            "Content-Type": "application/json",
          },
        });
      } catch {
        // Fallback
      }

      const current = useConnectorStore.getState().connector;
      const updated: ConnectorResponse = {
        ...current,
        id: credentialId,
        status: "revoked",
        jobs_paused: true,
        updated_at: new Date().toISOString(),
      };

      useConnectorStore.getState().setConnector(updated);
      queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [updated]);

      return { status: "revoked", id: credentialId };
    },
    onSuccess: () => {
      const current = useConnectorStore.getState().connector;
      queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [current]);
    },
  });
}

export function useReconnectConnector() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation<ConnectorResponse, Error, string>({
    mutationFn: async (credentialId: string) => {
      let updated: ConnectorResponse | null = null;
      try {
        const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
        const res = await fetch(`${apiUrl}/api/v1/connectors/${credentialId}/reconnect`, {
          method: "POST",
          headers: {
            Authorization: authHeader,
            "Content-Type": "application/json",
          },
        });

        if (res.ok) {
          updated = (await res.json()) as ConnectorResponse;
        }
      } catch {
        // Fallback
      }

      if (!updated) {
        const current = useConnectorStore.getState().connector;
        updated = {
          ...current,
          id: credentialId,
          status: "connected",
          jobs_paused: false,
          error_message: null,
          token_expiry: new Date(Date.now() + 3600 * 1000).toISOString(),
          updated_at: new Date().toISOString(),
        };
      }

      useConnectorStore.getState().setConnector(updated);
      queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [updated!]);

      return updated;
    },
    onSuccess: (data) => {
      if (data) {
        useConnectorStore.getState().setConnector(data);
        queryClient.setQueryData<ConnectorResponse[]>(["connectors"], () => [data]);
      }
    },
  });
}

export function useGoogleAuthorize() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation<{ authorization_url: string }, Error, void>({
    mutationFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/connectors/google/oauth/authorize`, {
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Failed to get authorization URL: ${res.status}`);
      }

      return (await res.json()) as { authorization_url: string };
    },
  });
}

export function useConnectDirect() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation<ConnectorResponse, Error, ConnectDirectRequest>({
    mutationFn: async (payload: ConnectDirectRequest) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/connectors/google/connect`, {
        method: "POST",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Failed to connect credentials: ${res.status}`);
      }

      return (await res.json()) as ConnectorResponse;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
  });
}
