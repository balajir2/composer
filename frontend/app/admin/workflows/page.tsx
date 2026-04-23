"use client";

import { useQuery } from "@tanstack/react-query";
import { listWorkflows } from "@/lib/api/workflows";
import { listAdminUsers, type AdminUser } from "@/lib/api/admin";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { ReassignOwnerDialog } from "@/components/composer/reassign-owner-dialog";
import { WorkflowVisibilityToggle } from "@/components/composer/workflow-visibility-toggle";
import { WorkflowProductionToggle } from "@/components/composer/workflow-production-toggle";

export default function AdminWorkflowsPage() {
  const workflowsQ = useQuery({
    queryKey: ["admin-all-workflows"],
    queryFn: () => listWorkflows({ limit: 100 }),
  });
  const usersQ = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => listAdminUsers(),
  });

  const userById = new Map<string, AdminUser>((usersQ.data ?? []).map((u) => [u.id, u]));

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-2xl font-semibold">All workflows</h2>
        <p className="text-sm text-muted-foreground">
          Click the visibility or production badges to toggle. Publishing
          requires a <strong>slug</strong> — the URL-safe identifier used when
          invoking the workflow via <code>POST /api/run/&#123;slug&#125;</code>{" "}
          with an API key.
        </p>
      </div>
      {workflowsQ.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : workflowsQ.isError || !workflowsQ.data ? (
        <EmptyState
          title="Could not load workflows"
          description="Try refreshing the page. If this keeps happening, check your network."
        />
      ) : workflowsQ.data.items.length === 0 ? (
        <EmptyState
          title="No workflows yet"
          description="Workflows will appear here once they have been created."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Visibility</TableHead>
              <TableHead>Production</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead className="w-32">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {workflowsQ.data.items.map((workflow) => {
              const owner = workflow.userId ? userById.get(workflow.userId) : undefined;
              return (
                <TableRow key={workflow.id}>
                  <TableCell className="font-medium">{workflow.name}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {owner ? (
                      <div>
                        <div>{owner.email}</div>
                        {owner.displayName && (
                          <div className="text-xs">{owner.displayName}</div>
                        )}
                      </div>
                    ) : (
                      <code className="rounded bg-muted px-2 py-1 font-mono text-xs">
                        {workflow.userId ?? "—"}
                      </code>
                    )}
                  </TableCell>
                  <TableCell>
                    <WorkflowVisibilityToggle
                      workflowId={workflow.id}
                      isPublic={workflow.isPublic}
                    />
                  </TableCell>
                  <TableCell>
                    <WorkflowProductionToggle
                      workflowId={workflow.id}
                      isProduction={Boolean(workflow.isProduction)}
                      currentSlug={workflow.externalSlug ?? null}
                    />
                  </TableCell>
                  <TableCell>
                    {workflow.externalSlug ? (
                      <code
                        className="rounded bg-muted px-2 py-1 font-mono text-xs text-muted-foreground"
                        title="URL path at /api/run/<slug> for API-key-authenticated external invoke"
                      >
                        {workflow.externalSlug}
                      </code>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <ReassignOwnerDialog
                      workflowId={workflow.id}
                      workflowName={workflow.name}
                    />
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
