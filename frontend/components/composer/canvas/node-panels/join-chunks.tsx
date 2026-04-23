"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";

export default function JoinChunksPanel({
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
          placeholder="state.chunks"
        />
        <p className="text-xs text-muted-foreground">State key holding the chunks array.</p>
      </div>

      <div className="space-y-2">
        <Label>Separator</Label>
        <Input
          value={(data.separator as string) ?? "\n"}
          onChange={(e) => onChange({ separator: e.target.value })}
          placeholder="\n"
        />
      </div>

      <div className="space-y-2">
        <Label>Prefix</Label>
        <Input
          value={(data.prefix as string) ?? ""}
          onChange={(e) => onChange({ prefix: e.target.value })}
          placeholder="Optional prefix"
        />
      </div>

      <div className="space-y-2">
        <Label>Suffix</Label>
        <Input
          value={(data.suffix as string) ?? ""}
          onChange={(e) => onChange({ suffix: e.target.value })}
          placeholder="Optional suffix"
        />
      </div>
    </div>
  );
}
