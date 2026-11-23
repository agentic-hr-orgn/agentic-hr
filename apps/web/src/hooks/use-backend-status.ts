"use client";

import { useHealthQuery } from "@/hooks/use-health";
import { useHydrated } from "@/hooks/use-hydrated";

export type BackendStatusKind = "live" | "standby" | "checking";

export function useBackendStatus() {
  const hydrated = useHydrated();
  const query = useHealthQuery();

  if (!hydrated) {
    return {
      status: "checking" as BackendStatusKind,
      health: null,
      isLive: false,
      isStandby: false,
      isChecking: true,
      useFakeIntegrations: true,
      refetch: query.refetch,
      isFetching: false,
    };
  }

  const isHealthy = query.data?.status === "healthy" || query.data?.status === "ok";

  let status: BackendStatusKind = "checking";
  if (query.data && isHealthy) {
    status = "live";
  } else if (query.isError || (query.data && !isHealthy)) {
    status = "standby";
  }

  return {
    status,
    health: query.data ?? null,
    isLive: status === "live",
    isStandby: status === "standby",
    isChecking: status === "checking",
    useFakeIntegrations: query.data?.use_fake_integrations ?? true,
    refetch: query.refetch,
    isFetching: query.isFetching,
  };
}
