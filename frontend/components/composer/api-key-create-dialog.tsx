"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createApiKey } from "@/lib/api/api-keys";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";

export function ApiKeyCreateDialog() {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [plaintext, setPlaintext] = useState<string | null>(null);
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: (l: string) => createApiKey({ label: l }),
    onSuccess: (res) => {
      setPlaintext(res.key);
      setLabel("");
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  function handleOpenChange(o: boolean) {
    setOpen(o);
    if (!o) setPlaintext(null);
  }

  function handleCopy() {
    if (plaintext === null) return;
    navigator.clipboard.writeText(plaintext).then(
      () => toast.success("Copied."),
      () => toast.error("Copy failed.")
    );
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>Create API key</Button>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create API key</DialogTitle>
            <DialogDescription>
              Used to invoke production workflows from outside the UI.
            </DialogDescription>
          </DialogHeader>
          {plaintext === null ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                mutation.mutate(label);
              }}
              className="space-y-4"
            >
              <div className="space-y-2">
                <Label htmlFor="key-label">Label</Label>
                <Input
                  id="key-label"
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="e.g. CI bot"
                  required
                />
              </div>
              <DialogFooter>
                <Button type="submit" disabled={mutation.isPending}>
                  {mutation.isPending ? "Creating…" : "Create"}
                </Button>
              </DialogFooter>
            </form>
          ) : (
            <div className="space-y-3">
              <p className="text-sm">Copy this key now. You won&apos;t be able to see it again.</p>
              <pre className="bg-muted break-all rounded-md p-3 text-xs">{plaintext}</pre>
              <DialogFooter>
                <Button onClick={handleCopy}>Copy</Button>
                <Button variant="outline" onClick={() => setOpen(false)}>
                  Done
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
