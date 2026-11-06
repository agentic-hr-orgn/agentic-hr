import createClient, { type Client } from "openapi-fetch";
import type { paths } from "./api-client";

/**
 * Creates a typed API client configured against the Agentic HR backend.
 * Uses NEXT_PUBLIC_API_URL or defaults to http://localhost:8000.
 */
export function createApiClient(baseUrl?: string): Client<paths> {
  const url =
    baseUrl ||
    (typeof process !== "undefined" && process.env?.NEXT_PUBLIC_API_URL) ||
    "http://localhost:8000";

  return createClient<paths>({ baseUrl: url });
}

/**
 * Pre-configured default API client instance for immediate consumption.
 */
export const apiClient: Client<paths> = createApiClient();
