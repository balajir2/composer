"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { FileUp } from "lucide-react";
import { createWorkflow } from "@/lib/api/workflows";
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
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import {
  parseWorkflowFromText,
  toWorkflowCreate,
} from "@/lib/workflow-import-export";

const MINIMAL_WF_BODY = {
  nodes: [
    {
      id: "start-1",
      type: "start" as const,
      position: { x: 0, y: 0 },
      data: { label: "Start" },
    },
    {
      id: "end-1",
      type: "end" as const,
      position: { x: 400, y: 0 },
      data: { label: "End" },
    },
  ],
  edges: [{ id: "e1", source: "start-1", target: "end-1" }],
  isTemplate: false,
  isPublic: false,
};

type Mode = "blank" | "import";

export function NewWorkflowDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("blank");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [importedFileName, setImportedFileName] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const blankMutation = useMutation({
    mutationFn: () =>
      createWorkflow({
        name: name.trim(),
        description: description.trim() || null,
        ...MINIMAL_WF_BODY,
      }),
    onSuccess: (created) => {
      reset();
      onOpenChange(false);
      router.push(`/designer/${created.id}`);
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Failed to create workflow."),
  });

  const importMutation = useMutation({
    mutationFn: async (file: File) => {
      const text = await file.text();
      const parsed = parseWorkflowFromText(text, file.name);
      return createWorkflow(toWorkflowCreate(parsed));
    },
    onSuccess: (created) => {
      reset();
      onOpenChange(false);
      toast.success(`Imported "${created.name}".`);
      router.push(`/designer/${created.id}`);
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Import failed."),
  });

  function reset() {
    setMode("blank");
    setName("");
    setDescription("");
    setImportedFileName(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    blankMutation.mutate();
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportedFileName(file.name);
    importMutation.mutate(file);
  }

  const importInFlight = importMutation.isPending;
  const blankInFlight = blankMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New workflow</DialogTitle>
          <DialogDescription>
            Start from scratch or import an existing workflow from a JSON or
            Markdown file.
          </DialogDescription>
        </DialogHeader>

        {/* Mode tabs */}
        <div className="flex gap-1 rounded-md bg-muted p-1">
          <button
            type="button"
            onClick={() => setMode("blank")}
            className={
              "flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors " +
              (mode === "blank"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground")
            }
            disabled={importInFlight || blankInFlight}
          >
            Blank
          </button>
          <button
            type="button"
            onClick={() => setMode("import")}
            className={
              "flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors " +
              (mode === "import"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground")
            }
            disabled={importInFlight || blankInFlight}
          >
            Import file
          </button>
        </div>

        {mode === "blank" ? (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="wf-name">Name</Label>
              <Input
                id="wf-name"
                placeholder="My workflow"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="wf-description">
                Description{" "}
                <span className="font-normal text-muted-foreground">(optional)</span>
              </Label>
              <Textarea
                id="wf-description"
                placeholder="What does this workflow do?"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={blankInFlight}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={!name.trim() || blankInFlight}>
                {blankInFlight ? "Creating…" : "Create"}
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="rounded-md border border-dashed bg-muted/30 p-4 text-center">
              <FileUp className="mx-auto mb-2 size-8 text-muted-foreground" />
              <p className="text-sm font-medium">
                Import a workflow from <code>.json</code> or <code>.md</code>
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                The file should be one Composer exported, or a JSON envelope
                with <code>name</code>, <code>nodes</code>, and{" "}
                <code>edges</code> at minimum.
              </p>
              <input
                ref={fileInputRef}
                type="file"
                accept=".json,.md,.markdown,application/json,text/markdown"
                onChange={handleFileSelect}
                className="hidden"
              />
              <Button
                type="button"
                className="mt-3"
                onClick={() => fileInputRef.current?.click()}
                disabled={importInFlight}
              >
                {importInFlight
                  ? `Importing ${importedFileName ?? "file"}…`
                  : "Choose file"}
              </Button>
              {importedFileName && !importInFlight && (
                <p className="mt-2 truncate text-xs text-muted-foreground">
                  {importedFileName}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={importInFlight}
              >
                Cancel
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
