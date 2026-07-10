"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { searchUsers } from "@/lib/api/users";
import {
  assignWorkflowUser,
  listWorkflowAssignments,
  unassignWorkflowUser,
} from "@/lib/api/workflows";

export function ManageAssigneesDialog({
  workflowId,
  workflowName,
}: {
  workflowId: string;
  workflowName: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const qc = useQueryClient();

  const assignmentsQ = useQuery({
    queryKey: ["workflow-assignments", workflowId],
    queryFn: () => listWorkflowAssignments(workflowId),
    enabled: open,
  });

  const searchQ = useQuery({
    queryKey: ["user-search", query],
    queryFn: () => searchUsers(query),
    enabled: open && query.trim().length >= 2,
  });

  const assignedIds = useMemo(
    () => new Set((assignmentsQ.data ?? []).map((a) => a.userId)),
    [assignmentsQ.data]
  );

  const grantMutation = useMutation({
    mutationFn: (userId: string) => assignWorkflowUser(workflowId, userId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workflow-assignments", workflowId] });
      toast.success("Access granted.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const revokeMutation = useMutation({
    mutationFn: (userId: string) => unassignWorkflowUser(workflowId, userId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workflow-assignments", workflowId] });
      toast.success("Access revoked.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const candidates = (searchQ.data ?? []).filter((u) => !assignedIds.has(u.id));

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Manage access
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Manage access to &quot;{workflowName}&quot;</DialogTitle>
            <DialogDescription>
              Anyone granted access can open, run, and edit this workflow.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label>Currently has access</Label>
            <div className="max-h-40 overflow-y-auto rounded-md border">
              {assignmentsQ.isLoading ? (
                <div className="p-3 text-sm text-muted-foreground">Loading…</div>
              ) : (assignmentsQ.data ?? []).length === 0 ? (
                <div className="p-3 text-sm text-muted-foreground">
                  No one else has access yet.
                </div>
              ) : (
                <ul className="divide-y">
                  {(assignmentsQ.data ?? []).map((a) => (
                    <li
                      key={a.userId}
                      className="flex items-center justify-between px-3 py-2 text-sm"
                    >
                      <code className="text-xs">{a.userId}</code>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Revoke access"
                        onClick={() => revokeMutation.mutate(a.userId)}
                        disabled={revokeMutation.isPending}
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="assignee-search">Grant access</Label>
            <Input
              id="assignee-search"
              placeholder="Search by email or name…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoComplete="off"
            />
            <div className="max-h-40 overflow-y-auto rounded-md border">
              {query.trim().length < 2 ? (
                <div className="p-3 text-sm text-muted-foreground">
                  Type at least 2 characters to search.
                </div>
              ) : searchQ.isLoading ? (
                <div className="p-3 text-sm text-muted-foreground">Searching…</div>
              ) : candidates.length === 0 ? (
                <div className="p-3 text-sm text-muted-foreground">No match.</div>
              ) : (
                <ul className="divide-y">
                  {candidates.map((u) => (
                    <li key={u.id} className="flex items-center justify-between px-3 py-2">
                      <div>
                        <div className="text-sm font-medium">{u.email}</div>
                        {u.displayName && (
                          <div className="text-xs text-muted-foreground">
                            {u.displayName}
                          </div>
                        )}
                      </div>
                      <Button
                        size="sm"
                        onClick={() => grantMutation.mutate(u.id)}
                        disabled={grantMutation.isPending}
                      >
                        Add
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
