import { forceRefresh, freshToken } from "../auth/keycloak";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** Network failures and 5xx are worth retrying; a refusal or a validation error is not. */
  get retryable(): boolean {
    return this.status === 0 || this.status >= 500;
  }
}

let onSessionEnded: () => void = () => undefined;
export function setSessionEndedHandler(handler: () => void): void {
  onSessionEnded = handler;
}

function normalise(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as Record<string, unknown>;
    return new ApiError(status, String(d.error ?? `http_${status}`), String(d.message ?? d.error ?? `HTTP ${status}`), d);
  }
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    return new ApiError(status, "validation_error", first?.msg ? `${first.loc?.slice(1).join(".") ?? "input"}: ${first.msg}` : "The request was not valid", { errors: detail });
  }
  return new ApiError(status, `http_${status}`, typeof detail === "string" ? detail : `The server answered ${status}`);
}

interface Options {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  query?: Record<string, string | number | boolean | undefined | null>;
}

function urlFor(path: string, query?: Options["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}?${text}` : path;
}

async function send(path: string, options: Options, token: string): Promise<Response> {
  return fetch(urlFor(path, options.query), {
    method: options.method ?? "GET",
    signal: options.signal,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
}

/** Authenticated JSON call. A 401 gets exactly one forced token refresh and retry; a second 401 ends the session. */
export async function api<T>(path: string, options: Options = {}): Promise<T> {
  let token: string;
  try {
    token = await freshToken();
  } catch {
    onSessionEnded();
    throw new ApiError(401, "session_ended", "Your session has ended");
  }
  let response: Response;
  try {
    response = await send(path, options, token);
    if (response.status === 401) {
      try {
        token = await forceRefresh();
      } catch {
        onSessionEnded();
        throw new ApiError(401, "session_ended", "Your session has ended");
      }
      response = await send(path, options, token);
      if (response.status === 401) {
        onSessionEnded();
        throw new ApiError(401, "session_ended", "Your session has ended");
      }
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network", "The API could not be reached");
  }
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) throw normalise(response.status, body);
  return body as T;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}
