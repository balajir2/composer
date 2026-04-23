/**
 * Low-level Composer backend helpers — no NextAuth dependency.
 * Safe to import in unit tests without mocking next-auth.
 */

const composerApiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

export type ComposerTokens = {
  accessToken: string;
  refreshToken: string;
  /** Unix epoch seconds when the access token expires. */
  accessTokenExpiresAt: number;
  /** Unix epoch seconds when the refresh token expires. */
  refreshTokenExpiresAt: number;
};

export type ComposerUserProfile = {
  id: string;
  email: string;
  role: "admin" | "member";
  displayName: string | null;
};

type TokenPairWire = {
  accessToken: string;
  refreshToken: string;
  accessTokenExpiresAt?: number;
  refreshTokenExpiresAt?: number;
};

/** Decode the `exp` claim from a JWT without verifying the signature.
 * Used as a fallback when the backend omits explicit expiry fields. */
function extractJwtExp(token: string): number | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const payload = JSON.parse(atob(parts[1]!.replace(/-/g, "+").replace(/_/g, "/"))) as {
      exp?: number;
    };
    return typeof payload.exp === "number" ? payload.exp : null;
  } catch {
    return null;
  }
}

function normalize(body: TokenPairWire): ComposerTokens {
  const nowSec = Math.floor(Date.now() / 1000);
  const accessExp =
    body.accessTokenExpiresAt ?? extractJwtExp(body.accessToken) ?? nowSec + 60 * 30;
  const refreshExp =
    body.refreshTokenExpiresAt ??
    extractJwtExp(body.refreshToken) ??
    nowSec + 60 * 60 * 24 * 7;
  return {
    accessToken: body.accessToken,
    refreshToken: body.refreshToken,
    accessTokenExpiresAt: accessExp,
    refreshTokenExpiresAt: refreshExp,
  };
}

export async function composerLogin(email: string, password: string): Promise<ComposerTokens> {
  const res = await fetch(`${composerApiUrl}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(`login failed: ${res.status}`);
  return normalize((await res.json()) as TokenPairWire);
}

export async function composerSsoExchange(azureToken: string): Promise<ComposerTokens> {
  const res = await fetch(`${composerApiUrl}/auth/sso-exchange`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ azureToken }),
  });
  if (!res.ok) throw new Error(`sso-exchange failed: ${res.status}`);
  return normalize((await res.json()) as TokenPairWire);
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
  return normalize((await res.json()) as TokenPairWire);
}
