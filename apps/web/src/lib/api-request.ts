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

export class ApiOfflineError extends Error {
  constructor(message = "OFFLINE: FastAPI backend is currently unreachable.") {
    super(message);
    this.name = "ApiOfflineError";
  }
}

export function isOfflineError(err: unknown): boolean {
  return (
    err instanceof ApiOfflineError || (err instanceof Error && err.message.startsWith("OFFLINE:"))
  );
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

function parseContentDispositionFilename(headerVal: string | null): string | null {
  if (!headerVal) return null;
  const utfMatch = headerVal.match(/filename\*=UTF-8''([^;]+)/i);
  if (utfMatch?.[1]) {
    try {
      return decodeURIComponent(utfMatch[1].trim());
    } catch {
      return utfMatch[1].trim();
    }
  }
  const plainMatch = headerVal.match(/filename="?([^";]+)"?/i);
  return plainMatch?.[1]?.trim() || null;
}

/**
 * Triggers a client-side file download from a Blob and immediately revokes the
 * temporary object URL inside `finally` to prevent memory leaks.
 */
export function triggerBrowserBlobDownload(blob: Blob, filename: string): void {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return;
  }
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

/**
 * Executes a binary download request against FastAPI with identical discriminated
 * live / http_error / offline semantics (never fabricates success on 4xx/5xx).
 */
export async function apiDownloadBlob(
  url: string,
  init?: RequestInit,
  fallbackPrefix = "Download failed"
): Promise<ApiRequestResult<{ blob: Blob; filename: string | null }>> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (err: unknown) {
    return { kind: "offline", error: err };
  }

  if (res.ok) {
    const blob = await res.blob();
    const filename = parseContentDispositionFilename(
      typeof res.headers?.get === "function" ? res.headers.get("content-disposition") : null
    );
    return { kind: "live", status: res.status, data: { blob, filename } };
  }

  let parsedBody: unknown = null;
  try {
    if (typeof res.json === "function") {
      parsedBody = await res.json();
    }
  } catch {
    parsedBody = null;
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
