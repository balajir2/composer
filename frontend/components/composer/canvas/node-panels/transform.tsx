"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export default function TransformPanel({
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
          placeholder="state.my_variable"
        />
      </div>

      <div className="space-y-2">
        <Label>Expression</Label>
        <Textarea
          value={(data.expression as string) ?? ""}
          onChange={(e) => onChange({ expression: e.target.value })}
          rows={4}
          placeholder="input.upper()"
          className="font-mono text-xs"
        />
        <p className="text-muted-foreground text-xs">
          simpleeval expression. Use <code>input</code> to reference the input variable.
        </p>
      </div>
    </div>
  );
}
