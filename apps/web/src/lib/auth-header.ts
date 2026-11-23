const DEV_MOCK_BEARER = "Bearer mock:agentic.hr.test.ubaid.owner@gmail.com:org_acme_tech_01";

/**
 * Returns true ONLY inside automated test runners (Vitest jsdom or Playwright WebDriver).
 * Always false in real user browser / Incognito sessions.
 */
export function isAutomatedTestSession(): boolean {
  if (
    typeof process !== "undefined" &&
    (process.env.NODE_ENV === "test" || process.env.VITEST === "true")
  ) {
    return true;
  }
  if (typeof navigator !== "undefined" && navigator.webdriver === true) {
    return true;
  }
  return false;
}

/**
 * Gates authenticated React Query hooks so live browser sessions wait until Clerk
 * finishes loading and has a signed-in user before firing API calls.
 */
export function isAuthQueryReady(isLoaded?: boolean, isSignedIn?: boolean): boolean {
  return Boolean((isLoaded && isSignedIn) || isAutomatedTestSession());
}

/**
 * Resolves the Authorization header for FastAPI calls.
 * - Real browser sessions strictly use the signed-in Clerk JWT and NEVER fall back to
 *   a hardcoded dev owner token while Clerk is loading or signed out.
 * - Automated test runners (Vitest / Playwright) retain the synthetic test bearer fallback.
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
      return null;
    }
    return null;
  }

  if (isAutomatedTestSession()) {
    return DEV_MOCK_BEARER;
  }

  return null;
}

export function formatBearerToken(authToken?: string | null): string | null {
  if (authToken) {
    return authToken.startsWith("Bearer ") ? authToken : `Bearer ${authToken}`;
  }
  if (isAutomatedTestSession()) {
    return DEV_MOCK_BEARER;
  }
  return null;
}
