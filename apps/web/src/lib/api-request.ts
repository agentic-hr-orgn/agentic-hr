export type ApiRequestResult<T> =
  | { kind: "live"; status: number; data: T }
  | {
      kind: "http_error";
      status: number;
      code: string;
      message: string;
      body: Record<string, unknown> | null;
    }
  | { kind: "offline"; error: unknown };

export class ApiHttpError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly body: Record<string, unknown> | null;

  constructor(
    status: number,
    code: string,
    message: string,
    body: Record<string, unknown> | null = null
  ) {
    super(message);
    this.name = "ApiHttpError";
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

export function extractApiError(
  body: Record<string, unknown> | null,
  status: number,
  fallbackPrefix = "Request failed"
): { code: string; message: string } {
  const errObj =
    body && typeof body.error === "object" && body.error !== null
      ? (body.error as Record<string, unknown>)
      : null;

  const code =
    (typeof errObj?.code === "string" && errObj.code) ||
    (typeof body?.code === "string" && body.code) ||
    `HTTP_${status}`;

  const message =
    (typeof errObj?.message === "string" && errObj.message) ||
    (typeof body?.detail === "string" && body.detail) ||
    (typeof body?.message === "string" && body.message) ||
    `${fallbackPrefix} (HTTP ${status})`;

  return { code, message };
}

/**
 * Executes a JSON HTTP request against FastAPI and discriminates cleanly between:
 * - "live": HTTP 2xx with parsed JSON body
 * - "http_error": Server reached but returned 4xx/5xx (parsed from unified {error:{code,message}} contract)
 * - "offline": Network / CORS / connection failure where server could not be reached
 */
export async function apiRequest<T>(
  url: string,
  init?: RequestInit,
  fallbackPrefix = "Request failed"
): Promise<ApiRequestResult<T>> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (err: unknown) {
    return { kind: "offline", error: err };
  }

  let parsedBody: unknown = null;
  try {
    if (typeof res.json === "function") {
      parsedBody = await res.json();
    }
  } catch {
    parsedBody = null;
  }

  if (res.ok) {
    return { kind: "live", status: res.status, data: parsedBody as T };
  }

  const bodyObj =
    parsedBody && typeof parsedBody === "object" ? (parsedBody as Record<string, unknown>) : null;
  const { code, message } = extractApiError(bodyObj, res.status, fallbackPrefix);

  return {
    kind: "http_error",
    status: res.status,
    code,
    message,
    body: bodyObj,
  };
}
