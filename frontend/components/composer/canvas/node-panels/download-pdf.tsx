"use client";

import type { Node as RFNode } from "reactflow";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { PromptField } from "../prompt-field";

const INPUT_FORMAT_OPTIONS = [
  { value: "html", label: "HTML" },
  { value: "markdown", label: "Markdown" },
];

export default function DownloadPdfPanel({
  data,
  onChange,
  allNodes,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  allNodes?: RFNode[];
  currentNodeId?: string;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="dlpdf-input-format">Input format</Label>
        <NativeSelect
          id="dlpdf-input-format"
          value={(data.inputFormat as string) ?? ""}
          onValueChange={(v) => onChange({ inputFormat: v })}
          options={INPUT_FORMAT_OPTIONS}
          placeholder="Select input format"
        />
      </div>

      <PromptField
        label="Content"
        value={(data.content as string) ?? ""}
        onChange={(next) => onChange({ content: next })}
        nodes={allNodes ?? []}
        currentNodeId={currentNodeId ?? ""}
        rows={8}
        placeholder="Reference an upstream node's output, e.g. {{narrative_agent}}"
      />

      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">Destination</p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="dlpdf-provider" className="text-xs">
              Provider
            </Label>
            <NativeSelect
              id="dlpdf-provider"
              value={(data.provider as string) ?? "local"}
              onValueChange={(v) => onChange({ provider: v })}
              options={[{ value: "local", label: "Local filesystem" }]}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="dlpdf-dest" className="text-xs">
              Destination path
            </Label>
            <Input
              id="dlpdf-dest"
              value={(data.destinationPath as string) ?? ""}
              onChange={(e) => onChange({ destinationPath: e.target.value })}
              placeholder="/out or {{output_dir}}"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="dlpdf-filename" className="text-xs">
              Filename (no extension)
            </Label>
            <Input
              id="dlpdf-filename"
              value={(data.filename as string) ?? ""}
              onChange={(e) => onChange({ filename: e.target.value })}
              placeholder="{{project_name}}-weekly-report"
              className="font-mono text-xs"
            />
            <p className="text-[10px] text-muted-foreground">
              The .pdf extension is added automatically. Must be a bare filename — no
              path separators or &quot;..&quot;.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
