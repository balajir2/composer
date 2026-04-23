"use client";

import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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
        <Label>Prompt</Label>
        <Textarea
          value={(data.prompt as string) ?? ""}
          onChange={(e) => onChange({ prompt: e.target.value })}
          rows={4}
          placeholder="Create a presentation about {{state.topic}}"
        />
      </div>

      <div className="space-y-2">
        <Label>Export as</Label>
        <Select
          value={(data.exportAs as string) ?? "pptx"}
          onValueChange={(v) => onChange({ exportAs: v })}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="pptx">PowerPoint (.pptx)</SelectItem>
            <SelectItem value="pdf">PDF</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}
