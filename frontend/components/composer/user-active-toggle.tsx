"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import {
  type AdminUser,
  deactivateUser,
  reactivateUser,
} from "@/lib/api/admin";

export function UserActiveToggle({ user }: { user: AdminUser }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const qc = useQueryClient();
  const active = user.isActive !== false;

  const deactivate = useMutation({
    mutationFn: () => deactivateUser(user.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      setConfirmOpen(false);
      toast.success(`${user.email} deactivated.`);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const reactivate = useMutation({
    mutationFn: () => reactivateUser(user.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      toast.success(`${user.email} reactivated.`);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  if (!active) {
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={() => reactivate.mutate()}
        disabled={reactivate.isPending}
      >
        {reactivate.isPending ? "Reactivating…" : "Reactivate"}
      </Button>
    );
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setConfirmOpen(true)}
      >
        Deactivate
      </Button>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Deactivate {user.email}?</DialogTitle>
            <DialogDescription>
              This immediately revokes all of the user&apos;s API keys and
              blocks future sign-ins. Their workflows, executions, and audit
              trail are preserved. You can reactivate them later.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={deactivate.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={() => deactivate.mutate()}
              disabled={deactivate.isPending}
            >
              {deactivate.isPending ? "Deactivating…" : "Deactivate"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
