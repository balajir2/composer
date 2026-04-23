"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";

export default function IfElsePanel({
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
          placeholder="state.score > 0.8"
          className="font-mono text-sm"
        />
        <p className="text-muted-foreground text-xs">
          simpleeval expression. Evaluates to true or false.
        </p>
      </div>
    </div>
  );
}
