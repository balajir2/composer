import NextAuth from "next-auth";
import AzureAD from "next-auth/providers/azure-ad";
import Credentials from "next-auth/providers/credentials";
import {
  composerLogin,
  composerSsoExchange,
  composerMe,
  composerRefresh,
} from "@/lib/composer-api";

export const { handlers, auth, signIn, signOut } = NextAuth({
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
      // First sign-in — stash Composer tokens on the JWT.
      if (user) {
        // Credentials path: `user` already carries composerAccessToken.
        const u = user as unknown as {
          composerAccessToken?: string;
          composerRefreshToken?: string;
          role?: "admin" | "member";
        };
        if (u.composerAccessToken) {
          token.composerAccessToken = u.composerAccessToken;
          token.composerRefreshToken = u.composerRefreshToken;
          token.role = u.role;
        }
      }
      // Azure path: on the first sign-in, `account` carries the Azure id_token.
      if (account?.provider === "azure-ad" && account.id_token && !token.composerAccessToken) {
        const tokens = await composerSsoExchange(account.id_token);
        const profile = await composerMe(tokens.accessToken);
        token.composerAccessToken = tokens.accessToken;
        token.composerRefreshToken = tokens.refreshToken;
        token.role = profile.role;
        token.sub = profile.id;
      }
      return token;
    },
    async session({ session, token }) {
      // Expose Composer access token + role to the client.
      (session as unknown as { accessToken?: string }).accessToken = token.composerAccessToken as
        | string
        | undefined;
      (session as unknown as { role?: "admin" | "member" }).role = token.role as
        | "admin"
        | "member"
        | undefined;
      if (token.sub) session.user = { ...session.user, id: token.sub } as never;
      return session;
    },
  },
  pages: { signIn: "/login" },
});

// Re-export for use in other modules (e.g., API client for token refresh).
export { composerRefresh };
