import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session extends DefaultSession {
    /** Short-lived bearer token presented to Composer. */
    accessToken?: string;
    /** Unix epoch seconds when `accessToken` expires.  NextAuth's jwt
     *  callback rotates the token proactively before this time. */
    accessTokenExpiresAt?: number;
    /** Unix epoch seconds when the refresh token expires.  After this,
     *  the user must re-authenticate. */
    refreshTokenExpiresAt?: number;
    role?: "admin" | "member";
    /** True when the account has a pending admin-forced password reset.
     *  A session with this set carries no usable Composer accessToken. */
    mustChangePassword?: boolean;
    /** Short-lived token that authorizes ONLY /auth/change-password.
     *  Present only when mustChangePassword is true. */
    passwordChangeToken?: string;
    /** Set to "RefreshAccessTokenError" when the JWT callback failed
     *  to refresh the access token and the session is effectively dead. */
    error?: "RefreshAccessTokenError";
    user?: {
      id?: string;
    } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    composerAccessToken?: string;
    composerRefreshToken?: string;
    accessTokenExpiresAt?: number;
    refreshTokenExpiresAt?: number;
    role?: "admin" | "member";
    mustChangePassword?: boolean;
    passwordChangeToken?: string;
    error?: "RefreshAccessTokenError";
  }
}
