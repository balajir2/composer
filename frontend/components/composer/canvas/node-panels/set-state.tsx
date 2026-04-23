"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";

export default function SetStatePanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>State key</Label>
        <Input
          value={(data.stateKey as string) ?? ""}
          onChange={(e) => onChange({ stateKey: e.target.value })}
          placeholder="my_variable"
        />
      </div>

      <div className="space-y-2">
        <Label>State value</Label>
        <Input
          value={(data.stateValue as string) ?? ""}
          onChange={(e) => onChange({ stateValue: e.target.value })}
          placeholder="{{previous_node.output}}"
        />
        <p className="text-xs text-muted-foreground">Supports {"{{variable}}"} substitution.</p>
      </div>
    </div>
  );
}
