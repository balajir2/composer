"use client";

import { useEffect, useMemo, useRef } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { createExecution } from "@/lib/api/executions";
import { startNodeSpec } from "@/lib/start-node-schema";
import { DocumentField } from "../document-field";
import { DatePickerInput, DateTimePickerInput } from "../date-field";

type CanvasWorkflow = {
  nodes: Array<{ type: string; data: Record<string, unknown> }>;
};

type FormValues = Record<string, unknown>;

export function RunDraftDialog({
  open,
  onOpenChange,
  workflowId,
  workflow,
  onStarted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowId: string;
  workflow: CanvasWorkflow;
  onStarted: (executionId: string) => void;
}) {
  const { fields, schema } = startNodeSpec(workflow);

  // Seed react-hook-form's internal state with defaults so that:
  //   (a) validation on submit doesn't see undefined for a field that has a
  //       visible default value, and
  //   (b) when the user types, RHF captures the edit into the same state
  //       that zodResolver will validate.
  //
  // Every declared field is present in the defaults (empty-string / false
  // for unset) so RHF considers them "registered" from mount.
  const defaultValues = useMemo<FormValues>(() => {
    const out: FormValues = {};
    for (const f of fields) {
      if (f.default !== undefined && f.default !== null) {
        if (f.type === "json" && typeof f.default !== "string") {
          out[f.name] = JSON.stringify(f.default, null, 2);
        } else if (f.type === "boolean") {
          out[f.name] = Boolean(f.default);
        } else {
          out[f.name] = String(f.default);
        }
      } else {
        out[f.name] = f.type === "boolean" ? false : "";
      }
    }
    return out;
    // fields is derived from the workflow prop; changing it means a different
    // Start-node spec, so we want a fresh default-values object.
  }, [fields]);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues,
    // Re-validate on change so the "Required" error clears the instant the
    // user starts typing (default is onSubmit-only after first error).
    mode: "onSubmit",
    reValidateMode: "onChange",
  });

  // Only reset the form on the open-transition (closed → open).  We
  // deliberately do NOT re-reset when `defaultValues` changes reference,
  // because the parent recomputes it on every render — and that would
  // wipe out whatever the user has typed mid-edit (see 2026-04-24 bug:
  // typed "Preetham Pura", backend received the default "Mahipalpur").
  const prevOpenRef = useRef(false);
  useEffect(() => {
    if (open && !prevOpenRef.current) {
      form.reset(defaultValues);
    }
    prevOpenRef.current = open;
    // `form` is a stable RHF object; `defaultValues` is intentionally read
    // at transition time only — don't add it to deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const hasDeclaredInputs =
    fields.length > 0 && !(fields.length === 1 && fields[0]?.name === "input");

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const input: Record<string, unknown> = {};
      for (const f of fields) {
        const raw = values[f.name];
        if (raw === undefined || raw === "") continue;
        if (f.type === "json" && typeof raw === "string") {
          try {
            input[f.name] = JSON.parse(raw);
          } catch {
            input[f.name] = raw;
          }
        } else if (f.type === "number" && typeof raw === "string") {
          const n = Number(raw);
          input[f.name] = Number.isFinite(n) ? n : raw;
        } else {
          input[f.name] = raw;
        }
      }
      // Free-form JSON shortcut (no declared inputs): send the parsed value
      // as the whole input rather than wrapping it under `input`.
      if (!hasDeclaredInputs) {
        const raw = values.input as string | undefined;
        if (raw && raw.trim()) {
          try {
            return createExecution({ workflowId, input: JSON.parse(raw) });
          } catch {
            return createExecution({ workflowId, input: raw });
          }
        }
        return createExecution({ workflowId, input: {} });
      }
      return createExecution({ workflowId, input });
    },
    onSuccess: (execution) => {
      onOpenChange(false);
      form.reset(defaultValues);
      onStarted(execution.id);
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Failed to start execution."),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Run draft</DialogTitle>
          <DialogDescription>
            {hasDeclaredInputs
              ? "Provide values for the workflow's declared Start inputs."
              : "Optionally provide a JSON payload; leave blank to run with no input."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={form.handleSubmit((v) => mutation.mutate(v))} className="space-y-3">
          {fields.map((f) => (
            <div key={f.name} className="space-y-1.5">
              <Label htmlFor={`draft-${f.name}`}>
                {f.label}
                {f.required && <span className="pl-0.5 text-destructive">*</span>}
              </Label>
              {f.type === "json" ? (
                <Textarea
                  id={`draft-${f.name}`}
                  rows={5}
                  {...form.register(f.name)}
                  placeholder='{"example": "value"}'
                  className="font-mono text-xs"
                />
              ) : f.type === "document" ? (
                <DocumentField
                  name={f.name}
                  required={f.required}
                  setValue={(v) => form.setValue(f.name, v)}
                  registered={form.register(f.name)}
                  idPrefix="draft"
                />
              ) : f.type === "boolean" ? (
                <input
                  id={`draft-${f.name}`}
                  type="checkbox"
                  {...form.register(f.name)}
                  className="h-4 w-4"
                />
              ) : f.type === "date" ? (
                <DatePickerInput
                  name={f.name}
                  value={(form.watch(f.name) as string) ?? ""}
                  setValue={(v) => form.setValue(f.name, v, { shouldValidate: true })}
                  registered={form.register(f.name)}
                  idPrefix="draft"
                />
              ) : f.type === "datetime" ? (
                <DateTimePickerInput
                  name={f.name}
                  value={(form.watch(f.name) as string) ?? ""}
                  setValue={(v) => form.setValue(f.name, v, { shouldValidate: true })}
                  registered={form.register(f.name)}
                  idPrefix="draft"
                />
              ) : (
                <Input
                  id={`draft-${f.name}`}
                  type={f.type === "number" ? "number" : "text"}
                  {...form.register(f.name)}
                />
              )}
              {form.formState.errors[f.name] && (
                <p className="text-xs text-destructive">
                  {String(form.formState.errors[f.name]?.message)}
                </p>
              )}
            </div>
          ))}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "Starting…" : "Run"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
