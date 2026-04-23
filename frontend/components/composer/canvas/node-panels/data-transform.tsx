"use client";

import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function DataTransformPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Expression</Label>
        <Textarea
          value={(data.expression as string) ?? ""}
          onChange={(e) => onChange({ expression: e.target.value })}
          rows={6}
          placeholder='{"result": "{{input.field}}"}'
          className="font-mono text-xs"
        />
        <p className="text-muted-foreground text-xs">
          JSON-path expression or Handlebars-style template.
        </p>
      </div>
    </div>
  );
}
