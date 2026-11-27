"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useSession } from "next-auth/react";

import { bulkDeleteExecutions, deleteExecution, listExecutions } from "@/lib/api/executions";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";

type DeleteIntent =
  | { kind: "single"; id: string; workflowId: string }
  | { kind: "bulk-selected"; ids: string[] }
  | { kind: "bulk-all" };

export default function HistoryPage() {
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  // Backend exposes role via the JWT claims NextAuth wraps; we read
  // it here purely to gate the "Delete all" button (a member who
  // clicks it gets a 403 anyway, so this is convenience not security).
  const isAdmin = (session?.user as { role?: string } | undefined)?.role === "admin";

  // Selected row ids — the source of truth for the "Delete selected"
  // button.  Use a Set for O(1) toggle; convert to Array on submit.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [intent, setIntent] = useState<DeleteIntent | null>(null);

  const [statusFilter, setStatusFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 100;
  const { data, isLoading, isError } = useQuery({
    queryKey: ["executions", statusFilter, limit, offset],
    queryFn: () => listExecutions({ limit, offset, status: statusFilter || undefined }),
    staleTime: 15_000,
  });

  const items = useMemo(() => data?.items ?? [], [data]);
  useEffect(() => {
    // A refetch/deletion may remove selected rows without a filter change.
    setSelected((previous) => {
      const visible = new Set(items.map((item) => item.id));
      const next = new Set([...previous].filter((id) => visible.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [items]);
  const allSelected = items.length > 0 && items.every((e) => selected.has(e.id));
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
    setSelected(allSelected ? new Set() : new Set(items.map((e) => e.id)));
  }

  const singleDelete = useMutation({
    mutationFn: (id: string) => deleteExecution(id),
    onSuccess: () => {
      toast.success("Execution deleted.");
      setIntent(null);
      void queryClient.invalidateQueries({ queryKey: ["executions"] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed to delete."),
  });

  const bulkDelete = useMutation({
    mutationFn: (body: { executionIds?: string[]; allInScope?: boolean }) =>
      bulkDeleteExecutions(body),
    onSuccess: (resp) => {
      const { deletedCount, skippedCount } = resp;
      if (skippedCount > 0) {
        toast.success(`Deleted ${deletedCount}; skipped ${skippedCount} not owned by you.`);
      } else {
        toast.success(`Deleted ${deletedCount} execution${deletedCount === 1 ? "" : "s"}.`);
      }
      setIntent(null);
      setSelected(new Set());
      void queryClient.invalidateQueries({ queryKey: ["executions"] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Bulk delete failed."),
  });

  function confirmIntent() {
    if (!intent) return;
    if (intent.kind === "single") {
      singleDelete.mutate(intent.id);
    } else if (intent.kind === "bulk-selected") {
      bulkDelete.mutate({ executionIds: intent.ids });
    } else {
      bulkDelete.mutate({ allInScope: true });
    }
  }

  const isPending = singleDelete.isPending || bulkDelete.isPending;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 pb-4">
        <div>
          <h2 className="text-2xl font-semibold">History</h2>
          <p className="text-xs text-muted-foreground">
            Members can delete their own executions; admins can delete any. Deleting also removes
            the LangGraph checkpoint trail.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {selected.size > 0 && (
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setIntent({ kind: "bulk-selected", ids: Array.from(selected) })}
              disabled={isPending}
            >
              <Trash2 className="mr-1.5 h-3.5 w-3.5" />
              Delete selected ({selected.size})
            </Button>
          )}
          {isAdmin && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIntent({ kind: "bulk-all" })}
              disabled={isPending}
              title="Admin only — wipes every execution on the system"
            >
              <Trash2 className="mr-1.5 h-3.5 w-3.5" />
              Delete all
            </Button>
          )}
        </div>
      </div>

      <div className="mb-4 flex items-center gap-2">
        <label htmlFor="history-status" className="text-sm font-medium">
          Status
        </label>
        <select
          id="history-status"
          value={statusFilter}
          className="rounded-md border bg-background px-3 py-2 text-sm"
          onChange={(event) => {
            setStatusFilter(event.target.value);
            setOffset(0);
            setSelected(new Set());
            setIntent(null);
          }}
        >
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="running">In progress</option>
          <option value="waiting_approval">Waiting for approval</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="canceled">Canceled</option>
        </select>
      </div>
      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : isError ? (
        <EmptyState
          title="Could not load history"
          description="Try refreshing the page. If this keeps happening, check your network."
        />
      ) : items.length === 0 ? (
        <EmptyState title="No runs yet" description="Executions you trigger will show up here." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <input
                  type="checkbox"
                  checked={allSelected}
                  ref={(el) => {
                    if (el) el.indeterminate = someSelected;
                  }}
                  onChange={toggleAll}
                  aria-label="Select all"
                  className="h-4 w-4"
                />
              </TableHead>
              <TableHead>Execution</TableHead>
              <TableHead>Workflow</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Started</TableHead>
              <TableHead className="w-12 text-right" aria-label="Actions"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((e) => (
              <TableRow key={e.id} className={selected.has(e.id) ? "bg-muted/40" : ""}>
                <TableCell>
                  <input
                    type="checkbox"
                    checked={selected.has(e.id)}
                    onChange={() => toggleOne(e.id)}
                    aria-label={`Select ${e.id}`}
                    className="h-4 w-4"
                  />
                </TableCell>
                <TableCell className="font-mono text-xs">
                  <Link
                    href={`/runs/${e.workflowId}/executions/${e.id}`}
                    className="text-primary hover:underline"
                  >
                    {e.id}
                  </Link>
                </TableCell>
                <TableCell className="text-sm">{e.workflowId}</TableCell>
                <TableCell>
                  <Badge variant={e.status === "failed" ? "destructive" : "secondary"}>
                    {e.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {new Date(String(e.startedAt)).toLocaleString()}
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() =>
                      setIntent({
                        kind: "single",
                        id: e.id,
                        workflowId: e.workflowId,
                      })
                    }
                    aria-label={`Delete execution ${e.id}`}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <nav aria-label="History pagination" className="mt-4 flex items-center gap-3">
        <Button
          variant="outline"
          aria-label="Previous page"
          disabled={offset === 0 || isLoading}
          onClick={() => {
            setOffset(Math.max(0, offset - limit));
            setSelected(new Set());
            setIntent(null);
          }}
        >
          Previous
        </Button>
        <span className="text-sm">Page {Math.floor(offset / limit) + 1}</span>
        <Button
          variant="outline"
          aria-label="Next page"
          disabled={isLoading || !data || offset + limit >= data.total}
          onClick={() => {
            setOffset(offset + limit);
            setSelected(new Set());
            setIntent(null);
          }}
        >
          Next
        </Button>
      </nav>

      <Dialog open={intent !== null} onOpenChange={(open) => !open && setIntent(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {intent?.kind === "bulk-all"
                ? "Delete all executions?"
                : intent?.kind === "bulk-selected"
                  ? `Delete ${intent.ids.length} executions?`
                  : "Delete execution?"}
            </DialogTitle>
            <DialogDescription>
              {intent?.kind === "single" && (
                <>
                  This permanently removes execution{" "}
                  <code className="font-mono text-xs">{intent.id}</code> from workflow{" "}
                  <code className="font-mono text-xs">{intent.workflowId}</code> along with its
                  approval history and LangGraph checkpoint trail.
                </>
              )}
              {intent?.kind === "bulk-selected" && (
                <>
                  This permanently removes the {intent.ids.length} selected execution
                  {intent.ids.length === 1 ? "" : "s"} along with their approval history and
                  LangGraph checkpoints. Any ids you don&apos;t own will be silently skipped.
                </>
              )}
              {intent?.kind === "bulk-all" && (
                <>
                  This permanently removes <strong>every execution</strong> on this deployment —
                  across all users and workflows. The workflows themselves are not affected. Use
                  this only when you really mean to wipe execution history globally (e.g. cleaning
                  up a staging environment).
                </>
              )}
              <br />
              <br />
              This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIntent(null)} disabled={isPending}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmIntent} disabled={isPending}>
              {isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
