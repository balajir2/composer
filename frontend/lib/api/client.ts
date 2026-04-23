import { getSession, signOut } from "next-auth/react";

const baseUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

export class ComposerApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
    message?: string
  ) {
    super(message ?? `Composer API ${status}`);
  }
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
