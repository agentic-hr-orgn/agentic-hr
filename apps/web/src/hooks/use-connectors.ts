"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  ConnectorResponse,
  ConnectDirectRequest,
  ReadOnlyToggleRequest,
} from "@agentic-hr/contracts";

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

  return useQuery<ConnectorResponse[], Error>({
    queryKey: ["connectors"],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/connectors`, {
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Failed to fetch connectors: ${res.status}`);
      }

      return (await res.json()) as ConnectorResponse[];
    },
    staleTime: 1000 * 30, // 30 seconds
    retry: 1,
  });
}

export function useToggleReadOnly() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation<ConnectorResponse, Error, { credentialId: string; isReadOnly: boolean }>({
    mutationFn: async ({ credentialId, isReadOnly }) => {
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

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Failed to toggle read-only mode: ${res.status}`);
      }

      return (await res.json()) as ConnectorResponse;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
  });
}

export function useRefreshConnector() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation<ConnectorResponse, Error, string>({
    mutationFn: async (credentialId: string) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/connectors/${credentialId}/refresh`, {
        method: "POST",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Failed to refresh connector token: ${res.status}`);
      }

      return (await res.json()) as ConnectorResponse;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
  });
}

export function useRevokeConnector() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  return useMutation<{ status: string; id: string }, Error, string>({
    mutationFn: async (credentialId: string) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await fetch(`${apiUrl}/api/v1/connectors/${credentialId}`, {
        method: "DELETE",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/json",
        },
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => null);
        throw new Error(errorData?.detail || `Failed to revoke connector: ${res.status}`);
      }

      return (await res.json()) as { status: string; id: string };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
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
