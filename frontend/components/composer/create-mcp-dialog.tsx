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
import { createMcpServer } from "@/lib/api/mcp-servers";

const AUTH_TYPES = [
  { value: "none", label: "None (public)" },
  { value: "api-key", label: "API key (custom header)" },
  { value: "bearer", label: "Bearer token" },
  { value: "oauth", label: "OAuth (configure after)" },
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

  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      createMcpServer({
        name,
        url,
        description: description || null,
        category: null,
        authType,
        accessToken: accessToken || null,
        headerName: headerName || null,
        isShared,
        headers: null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
      setOpen(false);
      setName("");
      setUrl("");
      setDescription("");
      setAuthType("none");
      setAccessToken("");
      setHeaderName("");
      setIsShared(false);
      toast.success("MCP server added.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const needsToken = authType === "api-key" || authType === "bearer";
  const needsHeaderName = authType === "api-key";

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button onClick={() => setOpen(true)}>Add MCP server</Button>
      <DialogContent className="max-w-lg">
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
          {authType === "oauth" && (
            <p className="text-xs text-muted-foreground">
              Create the server first, then complete the OAuth handshake from the
              server&apos;s row (after save).
            </p>
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
            <Button type="submit" disabled={mutation.isPending || !name || !url}>
              {mutation.isPending ? "Adding…" : "Add"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
