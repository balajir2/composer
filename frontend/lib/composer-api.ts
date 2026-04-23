/**
 * Low-level Composer backend helpers — no NextAuth dependency.
 * Safe to import in unit tests without mocking next-auth.
 */

const composerApiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

export type ComposerTokens = {
  accessToken: string;
  refreshToken: string;
};

export type ComposerUserProfile = {
  id: string;
  email: string;
  role: "admin" | "member";
  displayName: string | null;
};

export async function composerLogin(email: string, password: string): Promise<ComposerTokens> {
  const res = await fetch(`${composerApiUrl}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(`login failed: ${res.status}`);
  const body = (await res.json()) as {
    accessToken: string;
    refreshToken: string;
  };
  return { accessToken: body.accessToken, refreshToken: body.refreshToken };
}

export async function composerSsoExchange(azureToken: string): Promise<ComposerTokens> {
  const res = await fetch(`${composerApiUrl}/auth/sso-exchange`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ azureToken }),
  });
  if (!res.ok) throw new Error(`sso-exchange failed: ${res.status}`);
  const body = (await res.json()) as {
    accessToken: string;
    refreshToken: string;
  };
  return { accessToken: body.accessToken, refreshToken: body.refreshToken };
}

export async function composerMe(accessToken: string): Promise<ComposerUserProfile> {
  const res = await fetch(`${composerApiUrl}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error(`/auth/me failed: ${res.status}`);
  return (await res.json()) as ComposerUserProfile;
}

export async function composerRefresh(refreshToken: string): Promise<ComposerTokens> {
  const res = await fetch(`${composerApiUrl}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refreshToken }),
  });
  if (!res.ok) throw new Error(`refresh failed: ${res.status}`);
  const body = (await res.json()) as {
    accessToken: string;
    refreshToken: string;
  };
  return { accessToken: body.accessToken, refreshToken: body.refreshToken };
}
