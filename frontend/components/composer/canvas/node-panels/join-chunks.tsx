"use client";

import { useEffect } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";

export default function JoinChunksPanel({
  data,
  onChange,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  // Legacy-field migration (P0-0): the Designer used to write
  // inputVariable/separator/prefix/suffix instead of the canonical
  // joinChunks*-prefixed aliases JoinChunksNodeData actually reads.
  useEffect(() => {
    const patch: Record<string, unknown> = {};
    let dirty = false;
    if (data.joinChunksVariable === undefined && typeof data.inputVariable === "string") {
      patch.joinChunksVariable = data.inputVariable;
      patch.inputVariable = undefined;
      dirty = true;
    }
    if (data.joinChunksSeparator === undefined && typeof data.separator === "string") {
      patch.joinChunksSeparator = data.separator;
      patch.separator = undefined;
      dirty = true;
    }
    if (data.joinChunksPrefix === undefined && typeof data.prefix === "string") {
      patch.joinChunksPrefix = data.prefix;
      patch.prefix = undefined;
      dirty = true;
    }
    if (data.joinChunksSuffix === undefined && typeof data.suffix === "string") {
      patch.joinChunksSuffix = data.suffix;
      patch.suffix = undefined;
      dirty = true;
    }
    if (dirty) onChange(patch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentNodeId]);

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="join-chunks-variable">Input variable</Label>
        <Input
          id="join-chunks-variable"
          value={(data.joinChunksVariable as string) ?? ""}
          onChange={(e) => onChange({ joinChunksVariable: e.target.value })}
          placeholder="state.chunks"
        />
        <p className="text-xs text-muted-foreground">
          Name of the state variable holding the list of chunks — not a{" "}
          <code className="font-mono">{"{{template}}"}</code>, just the bare variable name.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="join-chunks-separator">Separator</Label>
        <Input
          id="join-chunks-separator"
          value={(data.joinChunksSeparator as string) ?? "\n\n"}
          onChange={(e) => onChange({ joinChunksSeparator: e.target.value })}
          placeholder="\n\n"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="join-chunks-prefix">Prefix</Label>
        <Input
          id="join-chunks-prefix"
          value={(data.joinChunksPrefix as string) ?? ""}
          onChange={(e) => onChange({ joinChunksPrefix: e.target.value })}
          placeholder="Optional prefix"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="join-chunks-suffix">Suffix</Label>
        <Input
          id="join-chunks-suffix"
          value={(data.joinChunksSuffix as string) ?? ""}
          onChange={(e) => onChange({ joinChunksSuffix: e.target.value })}
          placeholder="Optional suffix"
        />
      </div>

      <div className="flex items-center gap-2">
        <input
          id="join-chunks-include-metadata"
          type="checkbox"
          checked={Boolean(data.joinChunksIncludeMetadata)}
          onChange={(e) => onChange({ joinChunksIncludeMetadata: e.target.checked })}
          className="h-4 w-4"
        />
        <Label htmlFor="join-chunks-include-metadata">Include metadata</Label>
      </div>
    </div>
  );
}
