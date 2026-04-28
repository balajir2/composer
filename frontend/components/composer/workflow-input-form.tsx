"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import type { components } from "@/lib/api/generated/schema";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent } from "@/components/ui/card";
import { toast } from "sonner";
import { startNodeSpec } from "@/lib/start-node-schema";
import { createExecution } from "@/lib/api/executions";
import { extractDocumentText } from "@/lib/api/uploads";

type Workflow = components["schemas"]["WorkflowRead"];

/**
 * File picker for `document`-typed start inputs.  On select, uploads
 * the file to /uploads/extract-text, stashes the returned text as
 * the form value (which is what the engine ultimately consumes).
 *
 * The hidden input registered with react-hook-form is what zod
 * validates against — the visible <input type="file"> doesn't
 * carry the form state.  A cleaner pattern would use Controller,
 * but this matches how the rest of this form handles register/value.
 */
function DocumentField({
  name,
  required,
  setValue,
  registered,
}: {
  name: string;
  required: boolean;
  setValue: (value: string) => void;
  registered: ReturnType<ReturnType<typeof useForm>["register"]>;
}) {
  const [extracted, setExtracted] = useState<{
    filename: string;
    sizeBytes: number;
    chars: number;
  } | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  async function handleFile(file: File | null) {
    if (!file) {
      setExtracted(null);
      setValue("");
      return;
    }
    setIsUploading(true);
    try {
      const result = await extractDocumentText(file);
      setValue(result.text);
      setExtracted({
        filename: result.filename,
        sizeBytes: result.size_bytes,
        chars: result.text.length,
      });
      toast.success(
        `Extracted ${result.text.length.toLocaleString()} chars from ${result.filename}.`
      );
    } catch (err) {
      setValue("");
      setExtracted(null);
      toast.error(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <div className="space-y-1.5">
      {/* Hidden field carries the extracted text into form state for
          zod validation; the user never types into it directly. */}
      <input type="hidden" {...registered} />
      <Input
        id={`f-${name}`}
        type="file"
        accept=".txt,.md,.markdown,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        disabled={isUploading}
        onChange={(e) => {
          const file = e.target.files?.[0] ?? null;
          void handleFile(file);
        }}
      />
      {isUploading && (
        <p className="text-xs text-muted-foreground">
          Extracting text — this can take a few seconds for large PDFs.
        </p>
      )}
      {extracted && !isUploading && (
        <p className="text-xs text-muted-foreground">
          <strong>{extracted.filename}</strong> —{" "}
          {(extracted.sizeBytes / 1024).toFixed(1)}KB,{" "}
          {extracted.chars.toLocaleString()} chars extracted
        </p>
      )}
      {!extracted && !isUploading && (
        <p className="text-xs text-muted-foreground">
          Accepts .txt, .md, .pdf, .docx (max 10MB). Extracted text is
          passed to downstream nodes as a regular string variable.
          {required ? " Required." : ""}
        </p>
      )}
    </div>
  );
}

export function WorkflowInputForm({ workflow }: { workflow: Workflow }) {
  const router = useRouter();
  const { fields, schema } = startNodeSpec(
    workflow as unknown as { nodes: Array<{ type: string; data: Record<string, unknown> }> }
  );
  type FormValues = Record<string, unknown>;

  const form = useForm<FormValues>({ resolver: zodResolver(schema) });

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      // Serialize JSON string fields into real values; pass other types through.
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
        } else {
          input[f.name] = raw;
        }
      }
      // Single-json shortcut (the default when no declared inputs):
      // if the only field is 'input' of type json, send it directly
      // rather than wrapping in an extra key.
      const firstField = fields[0];
      if (fields.length === 1 && firstField?.name === "input" && firstField?.type === "json") {
        const raw = values.input as string | undefined;
        if (raw) {
          try {
            return createExecution({ workflowId: workflow.id, input: JSON.parse(raw) });
          } catch {
            return createExecution({ workflowId: workflow.id, input: raw });
          }
        }
        return createExecution({ workflowId: workflow.id, input: {} });
      }
      return createExecution({ workflowId: workflow.id, input });
    },
    onSuccess: (execution) => {
      router.push(`/runs/${workflow.id}/executions/${execution.id}`);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to start execution.");
    },
  });

  return (
    <Card>
      <CardContent className="pt-6">
        <form onSubmit={form.handleSubmit((v) => mutation.mutate(v))} className="space-y-4">
          {fields.map((f) => (
            <div key={f.name} className="space-y-2">
              <Label htmlFor={`f-${f.name}`}>
                {f.label}
                {f.required && <span className="pl-0.5 text-destructive">*</span>}
              </Label>
              {f.type === "json" ? (
                <Textarea
                  id={`f-${f.name}`}
                  rows={6}
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
                />
              ) : (
                <Input
                  id={`f-${f.name}`}
                  type={f.type === "number" ? "number" : f.type === "boolean" ? "checkbox" : "text"}
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
          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "Starting…" : "Run workflow"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
