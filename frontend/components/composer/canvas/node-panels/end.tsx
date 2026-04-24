"use client";

import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";

const RENDER_OPTIONS = [
  { value: "text", label: "Text" },
  { value: "markdown", label: "Markdown" },
  { value: "json", label: "JSON" },
];

export default function EndPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="end-render-hint">Output render hint</Label>
        <NativeSelect
          id="end-render-hint"
          value={(data.outputRenderHint as string) ?? "text"}
          onValueChange={(v) => onChange({ outputRenderHint: v })}
          options={RENDER_OPTIONS}
          placeholder="Select format"
        />
      </div>
    </div>
  );
}
