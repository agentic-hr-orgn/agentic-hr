const DEV_MOCK_BEARER = "Bearer mock:agentic.hr.test.ubaid.owner@gmail.com:org_acme_tech_01";

/**
 * Resolves the Authorization header for FastAPI calls.
 * Falls back to the synthetic dev owner mock token strictly in non-production environments.
 */
export async function getAuthHeader(
  getToken?: () => Promise<string | null>,
  isLoaded?: boolean,
  isSignedIn?: boolean
): Promise<string | null> {
  if (getToken && isLoaded && isSignedIn) {
    try {
      const token = await getToken();
      if (token) {
        return token.startsWith("Bearer ") ? token : `Bearer ${token}`;
      }
    } catch {
      // Token extraction error; fall through to dev check
    }
  }

  if (process.env.NODE_ENV !== "production") {
    return DEV_MOCK_BEARER;
  }

  return null;
}

export function formatBearerToken(authToken?: string | null): string | null {
  if (authToken) {
    return authToken.startsWith("Bearer ") ? authToken : `Bearer ${authToken}`;
  }
  if (process.env.NODE_ENV !== "production") {
    return DEV_MOCK_BEARER;
  }
  return null;
}
