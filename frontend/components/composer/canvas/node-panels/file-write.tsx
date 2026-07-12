"use client";

import type { Node as RFNode } from "reactflow";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { PromptField } from "../prompt-field";

export default function FileWritePanel({
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
      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">
          Destination
        </p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="fw-provider" className="text-xs">
              Provider
            </Label>
            <NativeSelect
              id="fw-provider"
              value={(data.provider as string) ?? "local"}
              onValueChange={(v) => onChange({ provider: v })}
              options={[{ value: "local", label: "Local filesystem" }]}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="fw-dest" className="text-xs">
              Destination path
            </Label>
            <Input
              id="fw-dest"
              value={(data.destinationPath as string) ?? ""}
              onChange={(e) => onChange({ destinationPath: e.target.value })}
              placeholder="/out or {{output_dir}}"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="fw-filename" className="text-xs">
              Filename (no extension)
            </Label>
            <Input
              id="fw-filename"
              value={(data.filename as string) ?? ""}
              onChange={(e) => onChange({ filename: e.target.value })}
              placeholder="{{project_name}}-BRD"
              className="font-mono text-xs"
            />
            <p className="text-[10px] text-muted-foreground">
              The extension is added automatically based on Format below. Must be a bare
              filename — no path separators or "..".
            </p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="fw-format" className="text-xs">
              Format
            </Label>
            <NativeSelect
              id="fw-format"
              value={(data.format as string) ?? "md"}
              onValueChange={(v) => onChange({ format: v })}
              options={[
                { value: "md", label: "Markdown (.md)" },
                { value: "docx", label: "Word (.docx)" },
                { value: "pdf", label: "PDF (.pdf)" },
              ]}
            />
          </div>
        </div>
      </div>

      <PromptField
        label="Content"
        value={(data.content as string) ?? ""}
        onChange={(next) => onChange({ content: next })}
        nodes={allNodes ?? []}
        currentNodeId={currentNodeId ?? ""}
        rows={8}
        placeholder="Reference an upstream node's output, e.g. {{draft_brd}}"
      />
    </div>
  );
}
