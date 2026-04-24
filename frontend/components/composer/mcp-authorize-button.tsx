"use client";

import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { oauthAuthorize } from "@/lib/api/mcp-servers";

/**
 * Starts the OAuth 2.1 authorization flow for an MCP server.
 *
 * Flow:
 *   1. POST /mcp-servers/{id}/oauth/authorize → returns authorize URL
 *      (includes PKCE challenge + RFC 8707 resource param; state stored
 *      server-side).
 *   2. Browser navigates to that URL; user consents at the provider.
 *   3. Provider redirects to the Composer backend's /oauth/callback,
 *      which exchanges the code and redirects back to /admin/mcp-servers
 *      with ?oauth=success or ?oauth=error&detail=...  The admin page's
 *      effect hook surfaces a toast.
 */
export function McpAuthorizeButton({
  serverId,
  serverName,
  hasOauthConfig,
  hasOauthToken,
}: {
  serverId: string;
  serverName: string;
  hasOauthConfig: boolean;
  hasOauthToken: boolean;
}) {
  const mutation = useMutation({
    mutationFn: () => {
      // Backend route mounted at /oauth/callback (GET).  Browsers must hit
      // the backend origin here, not the frontend, so the env var has to
      // point at the backend URL (e.g. http://localhost:8002).
      const apiBase =
        process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";
      return oauthAuthorize(serverId, {
        redirectUri: `${apiBase}/oauth/callback`,
      });
    },
    onSuccess: ({ authorizeUrl }) => {
      // Full-page redirect — the provider pages usually can't be embedded
      // in an iframe and popup windows are often blocked.  After the user
      // consents, the provider will redirect back to our /oauth/callback,
      // which redirects to /admin/mcp-servers with a status flag.
      window.location.href = authorizeUrl;
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Authorize failed.");
    },
  });

  if (!hasOauthConfig) {
    return (
      <Button
        size="sm"
        variant="outline"
        disabled
        title="Add OAuth config first (Authorize URL, Token URL, Client ID)"
      >
        No OAuth config
      </Button>
    );
  }

  return (
    <Button
      size="sm"
      variant={hasOauthToken ? "outline" : "default"}
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
      title={
        hasOauthToken
          ? `Already authorized — click to re-authorize ${serverName}`
          : `Sign in to ${serverName}`
      }
    >
      {mutation.isPending
        ? "Redirecting…"
        : hasOauthToken
          ? "Re-authorize"
          : "Authorize"}
    </Button>
  );
}
