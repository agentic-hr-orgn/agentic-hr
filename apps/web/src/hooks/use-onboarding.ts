"use client";

import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  OnboardingLaunchResponse,
  OnboardingState,
  OnboardingStepSaveRequest,
  ReadinessChecklist,
} from "@agentic-hr/contracts";
import { ApiHttpError, ApiOfflineError, apiRequest } from "@/lib/api-request";
import { getAuthHeader } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

function buildJsonHeaders(
  authHeader: string | null,
  extraHeaders?: Record<string, string>
): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(extraHeaders ?? {}),
  };
  if (authHeader) {
    headers.Authorization = authHeader;
  }
  return headers;
}

export function useOnboardingState() {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<OnboardingState, Error>({
    queryKey: ["onboarding", "state", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<OnboardingState>(
        `${API_BASE_URL}/api/v1/onboarding/state`,
        { headers: buildJsonHeaders(authHeader) },
        "Failed to load onboarding state"
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

export function useSaveOnboardingStep() {
  const queryClient = useQueryClient();
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useMutation<OnboardingState, Error, { step: number; payload: OnboardingStepSaveRequest }>({
    mutationFn: async ({ step, payload }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<OnboardingState>(
        `${API_BASE_URL}/api/v1/onboarding/steps/${step}`,
        {
          method: "PUT",
          headers: buildJsonHeaders(authHeader),
          body: JSON.stringify(payload),
        },
        `Failed to save onboarding step ${step}`
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; onboarding step could not be saved."
      );
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(["onboarding", "state", orgId], updated);
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
    },
  });
}

export function useOnboardingReadiness() {
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  return useQuery<ReadinessChecklist, Error>({
    queryKey: ["onboarding", "readiness", orgId],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<ReadinessChecklist>(
        `${API_BASE_URL}/api/v1/onboarding/readiness`,
        { headers: buildJsonHeaders(authHeader) },
        "Failed to check onboarding readiness"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError("OFFLINE: FastAPI backend is unreachable.");
    },
    enabled: isLoaded,
    staleTime: 1000 * 15,
    retry: false,
  });
}

export function useLaunchWorkspace() {
  const queryClient = useQueryClient();
  const { getToken, isLoaded, isSignedIn } = useAuth();

  return useMutation<OnboardingLaunchResponse, Error, { idempotencyKey: string }>({
    mutationFn: async ({ idempotencyKey }) => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const res = await apiRequest<OnboardingLaunchResponse>(
        `${API_BASE_URL}/api/v1/onboarding/launch`,
        {
          method: "POST",
          headers: buildJsonHeaders(authHeader, {
            "Idempotency-Key": idempotencyKey,
          }),
        },
        "Failed to launch workspace"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new ApiOfflineError(
        "OFFLINE: FastAPI backend is offline; workspace launch cannot be completed."
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["onboarding", "state"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding", "readiness"] });
    },
  });
}
