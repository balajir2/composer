"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
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
import { type AdminUser, resetUserPassword } from "@/lib/api/admin";

export function ResetPasswordDialog({ user }: { user: AdminUser }) {
  const [open, setOpen] = useState(false);
  const [tempPassword, setTempPassword] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => resetUserPassword(user.id),
    onSuccess: ({ temporaryPassword }) => {
      setTempPassword(temporaryPassword);
      toast.success(`Password reset for ${user.email}.`);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  function close() {
    setOpen(false);
    setTempPassword(null);
  }

  function handleCopy() {
    if (tempPassword === null) return;
    navigator.clipboard.writeText(tempPassword).then(
      () => toast.success("Copied."),
      () => toast.error("Copy failed.")
    );
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Reset password
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) {
            if (mutation.isPending) return;
            close();
          } else {
            setOpen(true);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset password for {user.email}</DialogTitle>
            <DialogDescription>
              {tempPassword
                ? "Share this temporary password with the user. It will not be shown again — they must change it on next login."
                : "Generates a random temporary password and forces the user to change it on their next sign-in."}
            </DialogDescription>
          </DialogHeader>
          {tempPassword && (
            <div className="rounded-md border bg-muted/40 p-3">
              <code className="break-all text-sm font-medium">{tempPassword}</code>
            </div>
          )}
          <DialogFooter>
            {tempPassword && <Button onClick={handleCopy}>Copy</Button>}
            <Button variant="outline" onClick={close} disabled={mutation.isPending}>
              {tempPassword ? "Close" : "Cancel"}
            </Button>
            {!tempPassword && (
              <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
                {mutation.isPending ? "Resetting…" : "Reset password"}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
