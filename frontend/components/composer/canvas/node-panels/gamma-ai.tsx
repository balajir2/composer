"use client";

import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";

const EXPORT_OPTIONS = [
  { value: "pptx", label: "PowerPoint (.pptx)" },
  { value: "pdf", label: "PDF" },
];

export default function GammaAiPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="gamma-prompt">Prompt</Label>
        <Textarea
          id="gamma-prompt"
          value={(data.prompt as string) ?? ""}
          onChange={(e) => onChange({ prompt: e.target.value })}
          rows={4}
          placeholder="Create a presentation about {{state.topic}}"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="gamma-export">Export as</Label>
        <NativeSelect
          id="gamma-export"
          value={(data.exportAs as string) ?? "pptx"}
          onValueChange={(v) => onChange({ exportAs: v })}
          options={EXPORT_OPTIONS}
        />
      </div>
    </div>
  );
}
