// Thin fetch wrapper - one function per DRF action, typed promises consumed
// by React Query hooks. No business logic here: it never interprets a
// gate verdict or pipeline state, only moves JSON.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include", // session auth (single-owner app)
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.detail ?? response.statusText);
  }
  return response.json() as Promise<T>;
}

// Every mutating call in this app is a stage-trigger POST (architecture
// plan §6/§12) - one thin helper, same error shape as apiGet so callers
// branch on ApiError.status (409 = ProjectBusyError) rather than parsing
// response bodies themselves.
// DRF's SessionAuthentication enforces Django's CSRF check on unsafe
// methods; the token travels as a plain (non-HttpOnly) cookie set when the
// user authenticates via /admin/login/, so we just relay it as a header.
function csrfToken(): string {
  const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const responseBody = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      responseBody.detail ?? summarizeFieldErrors(responseBody) ?? response.statusText,
    );
  }
  return response.json() as Promise<T>;
}

// DRF validation errors come back as {field: [messages]}, not {detail}.
function summarizeFieldErrors(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const entries = Object.entries(body as Record<string, unknown>);
  if (entries.length === 0) return null;
  return entries
    .map(([field, messages]) => `${field}: ${Array.isArray(messages) ? messages.join(", ") : messages}`)
    .join("; ");
}
