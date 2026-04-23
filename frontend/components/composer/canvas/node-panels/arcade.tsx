"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export default function ArcadePanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Tool name</Label>
        <Input
          value={(data.toolName as string) ?? ""}
          onChange={(e) => onChange({ toolName: e.target.value })}
          placeholder="Google.ListEmails"
        />
      </div>

      <div className="space-y-2">
        <Label>Args (JSON)</Label>
        <Textarea
          value={(data.args as string) ?? ""}
          onChange={(e) => onChange({ args: e.target.value })}
          rows={4}
          placeholder='{"n_emails": 5}'
          className="font-mono text-xs"
        />
      </div>
    </div>
  );
}
