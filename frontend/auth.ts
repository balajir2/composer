import NextAuth, { type NextAuthConfig } from "next-auth";
import type { JWT } from "next-auth/jwt";
import AzureAD from "next-auth/providers/azure-ad";
import Credentials from "next-auth/providers/credentials";
import {
  composerLogin,
  composerMe,
  composerRefresh,
  composerSsoExchange,
} from "@/lib/composer-api";

/**
 * Enterprise session management:
 *   - Access token: 8 h (backend-enforced).
 *   - Refresh token: 30 d (backend-enforced), rotating on every use.
 *   - NextAuth JWT callback refreshes access tokens proactively 60 s before
 *     expiry, so clients never see a 401 as long as the session is active.
 *   - If refresh fails (revoked / expired / backend down), sets
 *     token.error = "RefreshAccessTokenError"; the next useSession() sees it,
 *     and the API client forces a signOut on 401.
 */

const REFRESH_SKEW_SECONDS = 60;

type ComposerJwt = JWT & {
  composerAccessToken?: string;
  composerRefreshToken?: string;
  accessTokenExpiresAt?: number;
  refreshTokenExpiresAt?: number;
  role?: "admin" | "member";
  error?: "RefreshAccessTokenError";
};

async function refreshComposerTokens(token: ComposerJwt): Promise<ComposerJwt> {
  if (!token.composerRefreshToken) {
    return { ...token, error: "RefreshAccessTokenError" };
  }
  try {
    const tokens = await composerRefresh(token.composerRefreshToken);
    return {
      ...token,
      composerAccessToken: tokens.accessToken,
      composerRefreshToken: tokens.refreshToken,
      accessTokenExpiresAt: tokens.accessTokenExpiresAt,
      refreshTokenExpiresAt: tokens.refreshTokenExpiresAt,
      error: undefined,
    };
  } catch {
    return { ...token, error: "RefreshAccessTokenError" };
  }
}

export const authConfig: NextAuthConfig = {
  // NextAuth v5 enforces an "untrusted host" check in production — by
  // default it only trusts localhost.  Behind Cloud Run (or any reverse
  // proxy), the Host header is the proxy-rewritten public hostname,
  // which the library treats as untrusted and rejects with
  // `UntrustedHost: Host must be trusted`.  Setting trustHost: true
  // tells Auth.js to honour the host header the deployment platform
  // sets.  Same effect as AUTH_TRUST_HOST=true; checked in here so a
  // missing env var doesn't break a redeploy.
  trustHost: true,
  providers: [
    ...(process.env.AZURE_AD_CLIENT_ID && process.env.AZURE_AD_TENANT_ID
      ? [
          AzureAD({
            clientId: process.env.AZURE_AD_CLIENT_ID,
            clientSecret: process.env.AZURE_AD_CLIENT_SECRET!,
            issuer: `https://login.microsoftonline.com/${process.env.AZURE_AD_TENANT_ID}/v2.0`,
          }),
        ]
      : []),
    Credentials({
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(creds) {
        if (!creds?.email || !creds?.password) return null;
        try {
          const tokens = await composerLogin(creds.email as string, creds.password as string);
          const profile = await composerMe(tokens.accessToken);
          return {
            id: profile.id,
            email: profile.email,
            name: profile.displayName,
            composerAccessToken: tokens.accessToken,
            composerRefreshToken: tokens.refreshToken,
            accessTokenExpiresAt: tokens.accessTokenExpiresAt,
            refreshTokenExpiresAt: tokens.refreshTokenExpiresAt,
            role: profile.role,
          } as never;
        } catch {
          return null;
        }
      },
    }),
  ],
  session: { strategy: "jwt" },
  callbacks: {
    async jwt({ token, user, account }) {
      const t = token as ComposerJwt;

      // First sign-in (Credentials path) — user carries Composer tokens.
      if (user) {
        const u = user as unknown as {
          composerAccessToken?: string;
          composerRefreshToken?: string;
          accessTokenExpiresAt?: number;
          refreshTokenExpiresAt?: number;
          role?: "admin" | "member";
        };
        if (u.composerAccessToken) {
          t.composerAccessToken = u.composerAccessToken;
          t.composerRefreshToken = u.composerRefreshToken;
          t.accessTokenExpiresAt = u.accessTokenExpiresAt;
          t.refreshTokenExpiresAt = u.refreshTokenExpiresAt;
          t.role = u.role;
          t.error = undefined;
          return t;
        }
      }

      // First sign-in (Azure SSO path) — exchange id_token for Composer tokens.
      if (account?.provider === "azure-ad" && account.id_token && !t.composerAccessToken) {
        try {
          const tokens = await composerSsoExchange(account.id_token);
          const profile = await composerMe(tokens.accessToken);
          t.composerAccessToken = tokens.accessToken;
          t.composerRefreshToken = tokens.refreshToken;
          t.accessTokenExpiresAt = tokens.accessTokenExpiresAt;
          t.refreshTokenExpiresAt = tokens.refreshTokenExpiresAt;
          t.role = profile.role;
          t.sub = profile.id;
          t.error = undefined;
          return t;
        } catch {
          t.error = "RefreshAccessTokenError";
          return t;
        }
      }

      // Subsequent calls — proactively refresh if near or past expiry.
      const nowSec = Math.floor(Date.now() / 1000);
      if (t.refreshTokenExpiresAt && nowSec >= t.refreshTokenExpiresAt) {
        // Refresh window itself lapsed — user must re-authenticate.
        return { ...t, error: "RefreshAccessTokenError" };
      }
      if (
        t.accessTokenExpiresAt &&
        nowSec >= t.accessTokenExpiresAt - REFRESH_SKEW_SECONDS &&
        t.composerRefreshToken
      ) {
        return await refreshComposerTokens(t);
      }
      return t;
    },
    async session({ session, token }) {
      const t = token as ComposerJwt;
      const s = session as unknown as {
        accessToken?: string;
        accessTokenExpiresAt?: number;
        refreshTokenExpiresAt?: number;
        role?: "admin" | "member";
        error?: "RefreshAccessTokenError";
        user?: { id?: string };
      };
      // Expose minimum needed to the client — NEVER the refresh token.
      s.accessToken = t.composerAccessToken;
      s.accessTokenExpiresAt = t.accessTokenExpiresAt;
      s.refreshTokenExpiresAt = t.refreshTokenExpiresAt;
      s.role = t.role;
      s.error = t.error;
      if (t.sub) s.user = { ...s.user, id: t.sub };
      return session;
    },
  },
  pages: { signIn: "/login" },
};

export const { handlers, auth, signIn, signOut } = NextAuth(authConfig);

// Re-export for test imports.
export { composerRefresh };
