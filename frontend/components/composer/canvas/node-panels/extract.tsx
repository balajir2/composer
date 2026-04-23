"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export default function ExtractPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Input variable</Label>
        <Input
          value={(data.inputVariable as string) ?? ""}
          onChange={(e) => onChange({ inputVariable: e.target.value })}
          placeholder="state.text_output"
        />
      </div>

      <div className="space-y-2">
        <Label>Output schema (JSON)</Label>
        <Textarea
          value={(data.schema as string) ?? ""}
          onChange={(e) => onChange({ schema: e.target.value })}
          rows={6}
          placeholder='{"name": "string", "age": "number"}'
          className="font-mono text-xs"
        />
        <p className="text-muted-foreground text-xs">
          JSON schema describing the fields to extract.
        </p>
      </div>
    </div>
  );
}
