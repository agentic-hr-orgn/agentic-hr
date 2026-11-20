"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Department, DepartmentCreateRequest } from "@agentic-hr/contracts";
import { ApiHttpError, ApiOfflineError, apiRequest } from "@/lib/api-request";
import { getAuthHeader } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

function buildJsonHeaders(authHeader: string | null): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  return headers;
}

export function useDepartments() {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<Department[], Error>({
    queryKey: ["departments", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<Department[]>(
        `${API_BASE_URL}/api/v1/departments`,
        { headers: buildJsonHeaders(authHeader) },
        "Failed to load departments"
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

export function useCreateDepartment() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<Department, Error, DepartmentCreateRequest>({
    mutationFn: async (payload) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<Department>(
        `${API_BASE_URL}/api/v1/departments`,
        {
          method: "POST",
          headers: buildJsonHeaders(authHeader),
          body: JSON.stringify(payload),
        },
        "Failed to create department"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; department could not be created."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["departments"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
    },
  });
}

export function useDeleteDepartment() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<{ status: string; id: string }, Error, string>({
    mutationFn: async (departmentId) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<{ status: string; id: string }>(
        `${API_BASE_URL}/api/v1/departments/${departmentId}`,
        {
          method: "DELETE",
          headers: buildJsonHeaders(authHeader),
        },
        "Failed to remove department"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; department could not be removed."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["departments"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
    },
  });
}
