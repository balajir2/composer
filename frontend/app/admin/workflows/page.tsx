"use client";

import { useQuery } from "@tanstack/react-query";
import { listWorkflows } from "@/lib/api/workflows";
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
import { ReassignOwnerDialog } from "@/components/composer/reassign-owner-dialog";

export default function AdminWorkflowsPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-all-workflows"],
    queryFn: () => listWorkflows({ limit: 100 }),
  });

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">All workflows</h2>
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : isError || !data ? (
        <EmptyState
          title="Could not load workflows"
          description="Try refreshing the page. If this keeps happening, check your network."
        />
      ) : data.items.length === 0 ? (
        <EmptyState
          title="No workflows yet"
          description="Workflows will appear here once they have been created."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Owner ID</TableHead>
              <TableHead>Visibility</TableHead>
              <TableHead>Production</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead className="w-32">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.items.map((workflow) => (
              <TableRow key={workflow.id}>
                <TableCell className="font-medium">{workflow.name}</TableCell>
                <TableCell>
                  <code className="bg-muted text-muted-foreground rounded px-2 py-1 font-mono text-xs">
                    {workflow.userId ?? "—"}
                  </code>
                </TableCell>
                <TableCell>
                  <Badge variant={workflow.isPublic ? "default" : "secondary"}>
                    {workflow.isPublic ? "Public" : "Private"}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={workflow.isProduction ? "default" : "outline"}>
                    {workflow.isProduction ? "Yes" : "No"}
                  </Badge>
                </TableCell>
                <TableCell>
                  {workflow.externalSlug ? (
                    <code className="bg-muted text-muted-foreground rounded px-2 py-1 font-mono text-xs">
                      {workflow.externalSlug}
                    </code>
                  ) : (
                    <span className="text-muted-foreground text-xs">—</span>
                  )}
                </TableCell>
                <TableCell>
                  <ReassignOwnerDialog workflowId={workflow.id} workflowName={workflow.name} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
