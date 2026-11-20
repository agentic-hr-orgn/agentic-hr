"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { PolicyDocumentItem } from "@agentic-hr/contracts";
import { ApiHttpError, ApiOfflineError, apiRequest } from "@/lib/api-request";
import { getAuthHeader } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

function buildAuthHeaders(authHeader: string | null, json = false): Record<string, string> {
  const headers: Record<string, string> = {};
  if (json) {
    headers["Content-Type"] = "application/json";
  }
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  return headers;
}

export function usePolicies() {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<PolicyDocumentItem[], Error>({
    queryKey: ["policies", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<PolicyDocumentItem[]>(
        `${API_BASE_URL}/api/v1/policies`,
        { headers: buildAuthHeaders(authHeader, true) },
        "Failed to load policies"
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

export interface UploadPolicyInput {
  file: File;
  title?: string;
  storageProvider?: "local" | "google_drive";
}

export function useUploadPolicy() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<PolicyDocumentItem, Error, UploadPolicyInput>({
    mutationFn: async ({ file, title, storageProvider = "local" }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const formData = new FormData();
      formData.append("file", file);
      if (title && title.trim()) {
        formData.append("title", title.trim());
      }
      formData.append("storage_provider", storageProvider);

      const res = await apiRequest<PolicyDocumentItem>(
        `${API_BASE_URL}/api/v1/policies`,
        {
          method: "POST",
          headers: buildAuthHeaders(authHeader, false),
          body: formData,
        },
        "Failed to upload policy document"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; policy upload cannot proceed."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policies"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
    },
  });
}

export function useDeletePolicy() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<{ status: string; id: string }, Error, string>({
    mutationFn: async (policyId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<{ status: string; id: string }>(
        `${API_BASE_URL}/api/v1/policies/${policyId}`,
        {
          method: "DELETE",
          headers: buildAuthHeaders(authHeader, true),
        },
        "Failed to delete policy document"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; policy could not be deleted."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["policies"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
    },
  });
}
