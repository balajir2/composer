import type { JWT } from "next-auth/jwt";
import { composerMe, composerRefresh } from "@/lib/composer-api";

/**
 * Enterprise session management:
 *   - Access token: 8 h (backend-enforced).
 *   - Refresh token: 30 d (backend-enforced), rotating on every use.
 *   - Every refresh also re-fetches the current role from /auth/me, so a
 *     promotion/demotion made after login takes effect within one access-
 *     token cycle (~8h) instead of only on the refresh token's 30-day
 *     expiry forcing a full re-login.
 */

export type ComposerJwt = JWT & {
  composerAccessToken?: string;
  composerRefreshToken?: string;
  accessTokenExpiresAt?: number;
  refreshTokenExpiresAt?: number;
  role?: "admin" | "member";
  mustChangePassword?: boolean;
  passwordChangeToken?: string;
  error?: "RefreshAccessTokenError";
};

export async function refreshComposerTokens(token: ComposerJwt): Promise<ComposerJwt> {
  if (!token.composerRefreshToken) {
    return { ...token, error: "RefreshAccessTokenError" };
  }
  try {
    const tokens = await composerRefresh(token.composerRefreshToken);
    let role = token.role;
    try {
      const profile = await composerMe(tokens.accessToken);
      role = profile.role;
    } catch {
      // The token refresh itself already succeeded -- don't fail the
      // whole session over a secondary role lookup. Keep whatever role
      // the session already had rather than wiping it out.
    }
    return {
      ...token,
      composerAccessToken: tokens.accessToken,
      composerRefreshToken: tokens.refreshToken,
      accessTokenExpiresAt: tokens.accessTokenExpiresAt,
      refreshTokenExpiresAt: tokens.refreshTokenExpiresAt,
      role,
      error: undefined,
    };
  } catch {
    return { ...token, error: "RefreshAccessTokenError" };
  }
}
