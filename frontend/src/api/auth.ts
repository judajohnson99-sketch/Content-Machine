// The app's own front door (webapp/apps/accounts). Until this existed the
// SPA had no way to sign in: it told the user to visit Django's admin login
// and come back.
import { apiGet, apiPost, API_BASE_URL, ApiError } from "./client";

export interface Identity {
  username: string;
  email: string;
  /** The exact string a review decision will be attributed to. */
  reviewer: string;
}

export function getIdentity(): Promise<Identity> {
  return apiGet<Identity>("/auth/me/");
}

export function login(username: string, password: string): Promise<Identity> {
  return apiPost<Identity>("/auth/session/", { username, password });
}

export async function logout(): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/auth/session/`, {
    method: "DELETE",
    credentials: "include",
    headers: { "X-CSRFToken": csrfCookie() },
  });
  if (!response.ok) throw new ApiError(response.status, response.statusText);
}

function csrfCookie(): string {
  const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}
