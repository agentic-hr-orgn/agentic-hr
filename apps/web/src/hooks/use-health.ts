import { useQuery } from "@tanstack/react-query";
import { apiClient, type HealthResponse } from "@agentic-hr/contracts";

/**
 * TanStack Query hook that fetches health check telemetry from the FastAPI backend
 * using the generated OpenAPI TypeScript client.
 */
export function useHealthQuery() {
  return useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/health");
      if (error || !data) {
        throw new Error(
          typeof error === "string" ? error : "Failed to reach backend health endpoint"
        );
      }
      return data;
    },
    refetchInterval: 30000,
  });
}

