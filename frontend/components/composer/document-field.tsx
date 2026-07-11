"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { extractDocumentText } from "@/lib/api/uploads";

/**
 * File picker for `document`-typed start inputs.  On select, uploads
 * the file to /uploads/extract-text, stashes the returned text as
 * the form value (which is what the engine ultimately consumes).
 *
 * The hidden input registered with react-hook-form is what zod
 * validates against — the visible <input type="file"> doesn't
 * carry the form state.  A cleaner pattern would use Controller,
 * but this matches how the rest of this form handles register/value.
 *
 * Shared by WorkflowInputForm (the production run form) and
 * RunDraftDialog (the Designer's "Run draft" test dialog) — both
 * need identical document-upload behavior for `document`-typed
 * Start inputs.
 */
export function DocumentField({
  name,
  required,
  setValue,
  registered,
  idPrefix = "f",
}: {
  name: string;
  required: boolean;
  setValue: (value: string) => void;
  registered: ReturnType<ReturnType<typeof useForm>["register"]>;
  /** Must match the caller's <Label htmlFor> prefix for the label to
   *  correctly focus the file input. Defaults to "f" (WorkflowInputForm's
   *  convention); RunDraftDialog passes "draft" to match its own fields. */
  idPrefix?: string;
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
        id={`${idPrefix}-${name}`}
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
          <strong>{extracted.filename}</strong> — {(extracted.sizeBytes / 1024).toFixed(1)}KB,{" "}
          {extracted.chars.toLocaleString()} chars extracted
        </p>
      )}
      {!extracted && !isUploading && (
        <p className="text-xs text-muted-foreground">
          Accepts .txt, .md, .pdf, .docx (max 10MB). Extracted text is passed to downstream nodes as
          a regular string variable.
          {required ? " Required." : ""}
        </p>
      )}
    </div>
  );
}
