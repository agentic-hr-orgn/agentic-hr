"use client";

import { useAuth } from "@clerk/nextjs";
import { useQuery } from "@tanstack/react-query";
import { useHydrated } from "@/hooks/use-hydrated";
import { ApiHttpError, apiRequest } from "@/lib/api-request";
import { getAuthHeader, isAuthQueryReady } from "@/lib/auth-header";
import { API_BASE_URL } from "@/lib/platform";

export interface AuthenticatedUserContext {
  user_id: string;
  clerk_user_id: string;
  email: string;
  name: string | null;
  tenant_id: string;
  clerk_org_id: string;
  tenant_name: string;
  role: "owner" | "manager" | "employee" | string;
  is_hr_head: boolean;
  is_payroll_admin: boolean;
  permissions: string[];
}

export function useCurrentUser() {
  const hydrated = useHydrated();
  const { getToken, orgId, isLoaded, isSignedIn } = useAuth();

  const query = useQuery<AuthenticatedUserContext | null, Error>({
    queryKey: ["auth", "me", orgId, isSignedIn],
    queryFn: async () => {
      const authHeader = await getAuthHeader(getToken, isLoaded, isSignedIn);
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (authHeader) {
        headers.Authorization = authHeader;
      }

      const res = await apiRequest<AuthenticatedUserContext>(
        `${API_BASE_URL}/api/v1/auth/me`,
        { headers },
        "Auth context request failed"
      );

      if (res.kind === "live") return res.data;
      if (res.kind === "http_error") {
        throw new ApiHttpError(res.status, res.code, res.message, res.body);
      }
      throw new Error("OFFLINE: FastAPI backend is unreachable.");
    },
    enabled: hydrated && isAuthQueryReady(isLoaded, isSignedIn),
    staleTime: 1000 * 60 * 5,
    retry: 1,
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
