"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { createMcpServer, type OauthConfigInput } from "@/lib/api/mcp-servers";

const AUTH_TYPES = [
  { value: "none", label: "None (public)" },
  { value: "api-key", label: "API key (custom header)" },
  { value: "bearer", label: "Bearer token" },
  { value: "oauth", label: "OAuth 2.1 (PKCE)" },
];

export function CreateMcpDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [authType, setAuthType] = useState("none");
  const [accessToken, setAccessToken] = useState("");
  const [headerName, setHeaderName] = useState("");
  const [isShared, setIsShared] = useState(false);

  // OAuth fields
  const [authorizeUrl, setAuthorizeUrl] = useState("");
  const [tokenUrl, setTokenUrl] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [scopesRaw, setScopesRaw] = useState("");

  const qc = useQueryClient();

  function reset() {
    setName("");
    setUrl("");
    setDescription("");
    setAuthType("none");
    setAccessToken("");
    setHeaderName("");
    setIsShared(false);
    setAuthorizeUrl("");
    setTokenUrl("");
    setClientId("");
    setClientSecret("");
    setScopesRaw("");
  }

  const mutation = useMutation({
    mutationFn: () => {
      const oauthConfig: OauthConfigInput | undefined =
        authType === "oauth"
          ? {
              authorizeUrl,
              tokenUrl,
              clientId,
              clientSecret: clientSecret || null,
              scopes: scopesRaw
                .split(/[\s,]+/)
                .map((s) => s.trim())
                .filter(Boolean),
            }
          : undefined;
      return createMcpServer({
        name,
        url,
        description: description || null,
        category: null,
        authType,
        accessToken: accessToken || null,
        headerName: headerName || null,
        isShared,
        headers: null,
        ...(oauthConfig ? { oauthConfig } : {}),
      });
    },
    onSuccess: (server) => {
      qc.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
      setOpen(false);
      reset();
      if (authType === "oauth") {
        toast.success(
          `${server.name} added. Click "Authorize" on its row to complete the OAuth handshake.`,
          { duration: 8000 }
        );
      } else {
        toast.success("MCP server added.");
      }
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const needsToken = authType === "api-key" || authType === "bearer";
  const needsHeaderName = authType === "api-key";
  const isOauth = authType === "oauth";
  const oauthComplete =
    !isOauth ||
    (authorizeUrl.trim() !== "" && tokenUrl.trim() !== "" && clientId.trim() !== "");

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button onClick={() => setOpen(true)}>Add MCP server</Button>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Add an MCP server</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
          className="space-y-4"
        >
          <div className="space-y-2">
            <Label htmlFor="new-mcp-name">Name</Label>
            <Input
              id="new-mcp-name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Highspot"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-mcp-url">URL</Label>
            <Input
              id="new-mcp-url"
              required
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://mcp.example.com/v1"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-mcp-description">Description (optional)</Label>
            <Textarea
              id="new-mcp-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-mcp-auth-type">Auth type</Label>
            <NativeSelect
              id="new-mcp-auth-type"
              value={authType}
              onValueChange={setAuthType}
              options={AUTH_TYPES}
            />
          </div>
          {needsToken && (
            <div className="space-y-2">
              <Label htmlFor="new-mcp-token">
                {authType === "api-key" ? "API key value" : "Bearer token"}
              </Label>
              <Input
                id="new-mcp-token"
                type="password"
                autoComplete="off"
                value={accessToken}
                onChange={(e) => setAccessToken(e.target.value)}
                placeholder="paste token…"
              />
              <p className="text-xs text-muted-foreground">
                Stored encrypted at rest via AES-256-GCM.
              </p>
            </div>
          )}
          {needsHeaderName && (
            <div className="space-y-2">
              <Label htmlFor="new-mcp-header-name">Header name</Label>
              <Input
                id="new-mcp-header-name"
                value={headerName}
                onChange={(e) => setHeaderName(e.target.value)}
                placeholder="X-API-Key"
              />
            </div>
          )}
          {isOauth && (
            <div className="space-y-3 rounded-md border bg-muted/30 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                OAuth 2.1 config
              </p>
              <p className="text-xs text-muted-foreground">
                Provided by the MCP server&apos;s vendor. You can also edit
                these later from the server&apos;s row.
              </p>
              <div className="space-y-2">
                <Label htmlFor="new-mcp-authorize-url">Authorize URL</Label>
                <Input
                  id="new-mcp-authorize-url"
                  required={isOauth}
                  type="url"
                  value={authorizeUrl}
                  onChange={(e) => setAuthorizeUrl(e.target.value)}
                  placeholder="https://provider.example.com/oauth/authorize"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-mcp-token-url">Token URL</Label>
                <Input
                  id="new-mcp-token-url"
                  required={isOauth}
                  type="url"
                  value={tokenUrl}
                  onChange={(e) => setTokenUrl(e.target.value)}
                  placeholder="https://provider.example.com/oauth/token"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-mcp-client-id">Client ID</Label>
                <Input
                  id="new-mcp-client-id"
                  required={isOauth}
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  placeholder="abc123…"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-mcp-client-secret">
                  Client secret (optional for public clients)
                </Label>
                <Input
                  id="new-mcp-client-secret"
                  type="password"
                  autoComplete="off"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                  placeholder="paste secret…"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-mcp-scopes">Scopes</Label>
                <Input
                  id="new-mcp-scopes"
                  value={scopesRaw}
                  onChange={(e) => setScopesRaw(e.target.value)}
                  placeholder="read_all write_spot (space or comma separated)"
                />
              </div>
              <p className="text-xs text-muted-foreground">
                After saving, click <strong>Authorize</strong> on the server row
                to sign in and capture an access token.
              </p>
            </div>
          )}
          <div className="flex items-center gap-2">
            <input
              id="new-mcp-shared"
              type="checkbox"
              checked={isShared}
              onChange={(e) => setIsShared(e.target.checked)}
              className="h-4 w-4"
            />
            <Label htmlFor="new-mcp-shared" className="cursor-pointer">
              Share with all designers
            </Label>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={mutation.isPending || !name || !url || !oauthComplete}
            >
              {mutation.isPending ? "Adding…" : "Add"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
