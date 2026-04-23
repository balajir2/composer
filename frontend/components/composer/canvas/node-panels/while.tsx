"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";

export default function WhilePanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Condition</Label>
        <Input
          value={(data.condition as string) ?? ""}
          onChange={(e) => onChange({ condition: e.target.value })}
          placeholder="state.count < 5"
          className="font-mono text-sm"
        />
        <p className="text-xs text-muted-foreground">
          simpleeval expression. Loop continues while this is true.
        </p>
      </div>

      <div className="space-y-2">
        <Label>Max iterations</Label>
        <Input
          type="number"
          min={1}
          max={100}
          value={(data.maxIterations as number) ?? 10}
          onChange={(e) => onChange({ maxIterations: parseInt(e.target.value, 10) || 10 })}
          placeholder="10"
        />
        <p className="text-xs text-muted-foreground">Maximum 100.</p>
      </div>
    </div>
  );
}
