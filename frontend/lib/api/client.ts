import { getSession, signOut } from "next-auth/react";

const baseUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

export class ComposerApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
    message?: string
  ) {
    super(message ?? extractDetail(status, detail));
  }
}

function extractDetail(status: number, detail: unknown): string {
  // FastAPI returns {detail: string} for HTTPException, or
  // {detail: [{loc, msg, type}, ...]} for 422 validation errors.
  // Surface the server's message verbatim so users see WHY the call failed.
  if (detail && typeof detail === "object" && "detail" in detail) {
    const d = (detail as { detail: unknown }).detail;
    if (typeof d === "string") return `${status}: ${d}`;
    if (Array.isArray(d)) {
      const first = d[0] as { loc?: unknown[]; msg?: string } | undefined;
      if (first?.msg) {
        const loc = Array.isArray(first.loc) ? first.loc.join(".") : "";
        return `${status}: ${loc ? `${loc}: ` : ""}${first.msg}`;
      }
    }
  }
  return `Composer API ${status}`;
}

type Session = {
  accessToken?: string;
  accessTokenExpiresAt?: number;
  refreshTokenExpiresAt?: number;
  error?: "RefreshAccessTokenError";
} | null;

async function currentSession(): Promise<Session> {
  // `getSession()` triggers the NextAuth JWT callback, which proactively
  // refreshes the Composer access token when it's near expiry.  This
  // means fetch calls don't need their own refresh plumbing.
  return (await getSession()) as Session;
}

async function signOutAndThrow(status: number, detail: unknown): Promise<never> {
  try {
    await signOut({ redirect: true, callbackUrl: "/login?reason=session-expired" });
  } catch {
    /* redirect will still fire on unmount in most cases */
  }
  throw new ComposerApiError(status, detail, "Session expired — please sign in again.");
}

export async function apiFetch<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const session = await currentSession();

  // If the JWT callback couldn't refresh (revoked / expired), short-circuit.
  if (session?.error === "RefreshAccessTokenError") {
    return signOutAndThrow(401, { detail: session.error });
  }

  const headers = new Headers(opts.headers);
  if (session?.accessToken) {
    headers.set("Authorization", `Bearer ${session.accessToken}`);
  }
  if (opts.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${baseUrl}${path}`, { ...opts, headers });

  if (res.status === 401) {
    // Backend rejected the token despite our best efforts to keep it fresh.
    // Could happen if the backend JWT secret rotated, the user was
    // deactivated, or the token was revoked out-of-band.  Sign out cleanly.
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      /* ignore */
    }
    return signOutAndThrow(401, detail);
  }

  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      /* non-JSON error body */
    }
    throw new ComposerApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
