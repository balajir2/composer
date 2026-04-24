"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { deleteMcpServer, listMcpServers } from "@/lib/api/mcp-servers";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { CreateMcpDialog } from "@/components/composer/create-mcp-dialog";
import { McpSharedToggle } from "@/components/composer/mcp-shared-toggle";
import { McpTestButton } from "@/components/composer/mcp-test-button";
import { McpAuthorizeButton } from "@/components/composer/mcp-authorize-button";

export default function AdminMcpServersPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const search = useSearchParams();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-mcp-servers"],
    queryFn: () => listMcpServers(),
  });

  // Handle the ?oauth=success|error&detail=... query the backend sets on
  // /oauth/callback → /admin/mcp-servers redirect.
  useEffect(() => {
    const oauth = search?.get("oauth");
    if (!oauth) return;
    const detail = search.get("detail");
    if (oauth === "success") {
      toast.success("OAuth connected. You can now Test the server.");
      void qc.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
    } else {
      toast.error(`OAuth failed${detail ? `: ${detail}` : "."}`, {
        duration: 8000,
      });
    }
    // Scrub the query string so a refresh doesn't re-toast.
    router.replace("/admin/mcp-servers");
  }, [search, router, qc]);

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteMcpServer(id),
    onSuccess: () => {
      toast.success("MCP server deleted.");
      void qc.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">MCP servers</h2>
          <p className="text-sm text-muted-foreground">
            Model Context Protocol servers that expose tools to agents. For
            OAuth servers, click <strong>Authorize</strong> to sign in, then{" "}
            <strong>Test</strong> to verify the connection and refresh the tool
            list. Mark as &ldquo;shared&rdquo; to make them visible in every
            designer&apos;s Tools palette.
          </p>
        </div>
        <CreateMcpDialog />
      </div>
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : isError || !data ? (
        <EmptyState
          title="Could not load MCP servers"
          description="Try refreshing the page. If this keeps happening, check your network."
        />
      ) : data.length === 0 ? (
        <EmptyState
          title="No MCP servers yet"
          description="MCP servers will appear here once they have been added."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>URL</TableHead>
              <TableHead>Auth</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Last tested</TableHead>
              <TableHead className="w-28">Shared</TableHead>
              <TableHead className="w-72">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((server) => {
              const lastTested = server.lastTested
                ? new Date(server.lastTested as string).toLocaleString()
                : "—";
              const status = server.connectionStatus ?? "untested";
              const variant: "default" | "outline" | "destructive" =
                status === "connected"
                  ? "default"
                  : status === "error"
                    ? "destructive"
                    : "outline";
              const isOauth = server.authType === "oauth";
              const serverAny = server as unknown as {
                hasOauthConfig?: boolean;
                hasOauthToken?: boolean;
              };
              return (
                <TableRow key={server.id}>
                  <TableCell className="font-medium">{server.name}</TableCell>
                  <TableCell>
                    <code className="break-all text-xs text-muted-foreground">
                      {server.url}
                    </code>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{server.authType}</Badge>
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={variant}
                      title={server.lastError ?? undefined}
                    >
                      {status}
                    </Badge>
                    {server.lastError && (
                      <div
                        className="mt-1 max-w-xs truncate text-xs text-destructive"
                        title={server.lastError}
                      >
                        {server.lastError}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {lastTested}
                  </TableCell>
                  <TableCell>
                    <McpSharedToggle
                      serverId={server.id}
                      serverName={server.name}
                      isShared={server.isShared}
                    />
                  </TableCell>
                  <TableCell className="flex flex-wrap items-center gap-2">
                    {isOauth && (
                      <McpAuthorizeButton
                        serverId={server.id}
                        serverName={server.name}
                        hasOauthConfig={Boolean(serverAny.hasOauthConfig)}
                        hasOauthToken={Boolean(serverAny.hasOauthToken)}
                      />
                    )}
                    <McpTestButton
                      serverId={server.id}
                      serverName={server.name}
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        if (
                          window.confirm(
                            `Delete "${server.name}"? This cannot be undone.`
                          )
                        ) {
                          deleteMutation.mutate(server.id);
                        }
                      }}
                      disabled={deleteMutation.isPending}
                    >
                      Delete
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
