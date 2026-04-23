import { auth, signOut } from "@/auth";
import { composerRefresh } from "@/lib/composer-api";

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

async function getAccessToken(): Promise<string | null> {
  const session = await auth();
  return (session as unknown as { accessToken?: string } | null)?.accessToken ?? null;
}

type FetchOpts = RequestInit & {
  /** When true, don't attempt refresh on 401 (used by the refresh call itself). */
  skipRefresh?: boolean;
};

export async function apiFetch<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const token = await getAccessToken();
  const headers = new Headers(opts.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (opts.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${baseUrl}${path}`, { ...opts, headers });

  if (res.status === 401 && !opts.skipRefresh) {
    // Attempt a single refresh.  On success retry once; on failure, sign out.
    const session = await auth();
    const refreshToken = (session as unknown as { refreshToken?: string } | null)?.refreshToken;
    if (refreshToken) {
      try {
        await composerRefresh(refreshToken);
        return apiFetch<T>(path, { ...opts, skipRefresh: true });
      } catch {
        await signOut();
        throw new ComposerApiError(401, null, "authentication expired");
      }
    }
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
