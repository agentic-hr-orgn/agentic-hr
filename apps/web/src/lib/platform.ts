export const PLATFORM_STAGE = "Stage 4 ✓ · Stage 5 next";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function getApiHostLabel(): string {
  try {
    const parsed = new URL(API_BASE_URL);
    return parsed.host;
  } catch {
    return API_BASE_URL.replace(/^https?:\/\//, "");
  }
}
