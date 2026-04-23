"use client";

import { useQuery } from "@tanstack/react-query";
import { listMcpServers } from "@/lib/api/mcp-servers";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { McpSharedToggle } from "@/components/composer/mcp-shared-toggle";

export default function AdminMcpServersPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-mcp-servers"],
    queryFn: () => listMcpServers(),
  });

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">MCP servers</h2>
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
              <TableHead className="w-36">Shared</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((server) => (
              <TableRow key={server.id}>
                <TableCell className="font-medium">{server.name}</TableCell>
                <TableCell>
                  <code className="text-muted-foreground break-all text-xs">{server.url}</code>
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{server.authType}</Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={server.connectionStatus === "connected" ? "default" : "outline"}>
                    {server.connectionStatus}
                  </Badge>
                </TableCell>
                <TableCell>
                  <McpSharedToggle
                    serverId={server.id}
                    serverName={server.name}
                    isShared={server.isShared}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
