"use client";

import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function NotePanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Content (Markdown)</Label>
        <Textarea
          value={(data.content as string) ?? ""}
          onChange={(e) => onChange({ content: e.target.value })}
          rows={8}
          placeholder="# Note&#10;&#10;Write your notes here…"
          className="font-mono text-xs"
        />
        <p className="text-muted-foreground text-xs">Visual-only — no runtime effect.</p>
      </div>
    </div>
  );
}
