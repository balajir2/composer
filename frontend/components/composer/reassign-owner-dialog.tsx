"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

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
import { listAdminUsers, type AdminUser } from "@/lib/api/admin";
import { reassignWorkflowOwner } from "@/lib/api/workflows";

export function ReassignOwnerDialog({
  workflowId,
  workflowName,
}: {
  workflowId: string;
  workflowName: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<AdminUser | null>(null);
  const qc = useQueryClient();

  const usersQ = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => listAdminUsers(),
    enabled: open,
  });

  const candidates = useMemo(() => {
    if (!usersQ.data) return [];
    const active = usersQ.data.filter((u) => u.isActive !== false);
    const q = query.trim().toLowerCase();
    if (!q) return active.slice(0, 20);
    return active
      .filter((u) => {
        const name = (u.displayName ?? "").toLowerCase();
        return u.email.toLowerCase().includes(q) || name.includes(q);
      })
      .slice(0, 20);
  }, [usersQ.data, query]);

  const mutation = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("Select a user first.");
      return reassignWorkflowOwner(workflowId, { userId: selected.id });
    },
    onSuccess: () => {
      setOpen(false);
      setQuery("");
      setSelected(null);
      toast.success("Workflow owner reassigned.");
      void qc.invalidateQueries({ queryKey: ["admin-all-workflows"] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Reassign
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reassign workflow owner</DialogTitle>
            <DialogDescription>
              Assign &quot;{workflowName}&quot; to a new owner. Search by email
              or display name.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="reassign-search">User</Label>
            <Input
              id="reassign-search"
              placeholder="Type to filter users…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelected(null);
              }}
              autoComplete="off"
            />
            <div className="max-h-60 overflow-y-auto rounded-md border">
              {usersQ.isLoading ? (
                <div className="p-3 text-sm text-muted-foreground">Loading users…</div>
              ) : candidates.length === 0 ? (
                <div className="p-3 text-sm text-muted-foreground">
                  {query ? "No match" : "No users"}
                </div>
              ) : (
                <ul className="divide-y">
                  {candidates.map((u) => (
                    <li key={u.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setSelected(u);
                          setQuery(u.email);
                        }}
                        className={`w-full px-3 py-2 text-left text-sm hover:bg-muted ${
                          selected?.id === u.id ? "bg-muted" : ""
                        }`}
                      >
                        <div className="font-medium">{u.email}</div>
                        {u.displayName && (
                          <div className="text-xs text-muted-foreground">
                            {u.displayName}
                          </div>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => mutation.mutate()}
              disabled={mutation.isPending || !selected}
            >
              {mutation.isPending ? "Reassigning…" : "Reassign"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
