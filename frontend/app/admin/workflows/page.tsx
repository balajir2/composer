"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { deleteWorkflow, listWorkflows } from "@/lib/api/workflows";
import { listAdminUsers, type AdminUser } from "@/lib/api/admin";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { ReassignOwnerDialog } from "@/components/composer/reassign-owner-dialog";
import { WorkflowVisibilityToggle } from "@/components/composer/workflow-visibility-toggle";
import { WorkflowProductionToggle } from "@/components/composer/workflow-production-toggle";

export default function AdminWorkflowsPage() {
  const qc = useQueryClient();
  const workflowsQ = useQuery({
    queryKey: ["admin-all-workflows"],
    queryFn: () => listWorkflows({ limit: 100 }),
  });
  const usersQ = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => listAdminUsers(),
  });

  const [selected, setSelected] = useState<Set<string>>(new Set());

  const items = useMemo(() => workflowsQ.data?.items ?? [], [workflowsQ.data]);
  const allSelected = items.length > 0 && selected.size === items.length;
  const someSelected = selected.size > 0 && !allSelected;

  function toggleOne(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) => {
      if (prev.size === items.length) return new Set();
      return new Set(items.map((w) => w.id));
    });
  }

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteWorkflow(id),
    onSuccess: () => {
      toast.success("Workflow deleted.");
      void qc.invalidateQueries({ queryKey: ["admin-all-workflows"] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const bulkDeleteMutation = useMutation({
    mutationFn: async (ids: string[]) => {
      const results = await Promise.allSettled(ids.map((id) => deleteWorkflow(id)));
      const failed = results
        .map((r, i) => (r.status === "rejected" ? { id: ids[i]!, reason: r.reason } : null))
        .filter((x): x is { id: string; reason: unknown } => x !== null);
      return { total: ids.length, failedCount: failed.length, failed };
    },
    onSuccess: ({ total, failedCount, failed }) => {
      setSelected(new Set());
      void qc.invalidateQueries({ queryKey: ["admin-all-workflows"] });
      if (failedCount === 0) {
        toast.success(`Deleted ${total} workflow${total === 1 ? "" : "s"}.`);
      } else if (failedCount === total) {
        toast.error(
          `All ${total} deletions failed. ${
            failed[0]?.reason instanceof Error ? failed[0].reason.message : ""
          }`
        );
      } else {
        toast.warning(
          `Deleted ${total - failedCount} of ${total}. ${failedCount} failed — see console for details.`
        );
        console.warn("bulk delete partial failures:", failed);
      }
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Bulk delete failed."),
  });

  const selectedNames = useMemo(
    () => items.filter((w) => selected.has(w.id)).map((w) => w.name),
    [items, selected]
  );

  const userById = new Map<string, AdminUser>((usersQ.data ?? []).map((u) => [u.id, u]));
  const isBusy = deleteMutation.isPending || bulkDeleteMutation.isPending;

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

      {selected.size > 0 && (
        <div className="flex items-center justify-between gap-3 rounded-md border bg-muted/40 px-3 py-2">
          <span className="text-sm">
            <strong>{selected.size}</strong> selected
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setSelected(new Set())}
              disabled={isBusy}
            >
              Clear
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => {
                const preview = selectedNames.slice(0, 5).join(", ");
                const more = selectedNames.length > 5 ? ` and ${selectedNames.length - 5} more` : "";
                if (
                  window.confirm(
                    `Delete ${selected.size} workflow${selected.size === 1 ? "" : "s"}?\n\n${preview}${more}\n\nThis also removes all their executions and cannot be undone.`
                  )
                ) {
                  bulkDeleteMutation.mutate(Array.from(selected));
                }
              }}
              disabled={isBusy}
            >
              {bulkDeleteMutation.isPending
                ? `Deleting ${selected.size}…`
                : `Delete ${selected.size}`}
            </Button>
          </div>
        </div>
      )}

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
              <TableHead className="w-10">
                <input
                  type="checkbox"
                  aria-label="Select all workflows"
                  checked={allSelected}
                  ref={(el) => {
                    if (el) el.indeterminate = someSelected;
                  }}
                  onChange={toggleAll}
                  className="h-4 w-4 cursor-pointer"
                />
              </TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Visibility</TableHead>
              <TableHead>Production</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead className="w-48">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {workflowsQ.data.items.map((workflow) => {
              const owner = workflow.userId ? userById.get(workflow.userId) : undefined;
              const isSelected = selected.has(workflow.id);
              return (
                <TableRow key={workflow.id} data-state={isSelected ? "selected" : undefined}>
                  <TableCell>
                    <input
                      type="checkbox"
                      aria-label={`Select ${workflow.name}`}
                      checked={isSelected}
                      onChange={() => toggleOne(workflow.id)}
                      className="h-4 w-4 cursor-pointer"
                    />
                  </TableCell>
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
                  <TableCell className="flex items-center gap-2">
                    <ReassignOwnerDialog
                      workflowId={workflow.id}
                      workflowName={workflow.name}
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        if (
                          window.confirm(
                            `Delete workflow "${workflow.name}"? This also removes its executions and cannot be undone.`
                          )
                        ) {
                          deleteMutation.mutate(workflow.id);
                        }
                      }}
                      disabled={isBusy}
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
