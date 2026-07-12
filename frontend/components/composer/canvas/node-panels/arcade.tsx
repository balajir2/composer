"use client";

import { useEffect, useState } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

function stringifyInput(input: unknown): string {
  if (input && typeof input === "object" && Object.keys(input).length > 0) {
    return JSON.stringify(input, null, 2);
  }
  return "";
}

function parseInput(text: string): { value?: Record<string, unknown>; error?: string } {
  if (!text.trim()) return { value: {} };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: "Invalid JSON." };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { error: "Args must be a JSON object." };
  }
  return { value: parsed as Record<string, unknown> };
}

export default function ArcadePanel({
  data,
  onChange,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  // Legacy-field migration (P0-0): the Designer used to write
  // toolName/args instead of the canonical arcadeTool/arcadeInput aliases
  // the backend actually reads (ArcadeNodeData.tool/input), so a
  // UI-configured Arcade node always failed at runtime with "arcadeTool is
  // required". Backfill and drop the legacy keys the moment this node is
  // opened. Never overwrites an already-populated canonical field.
  useEffect(() => {
    const patch: Record<string, unknown> = {};
    let dirty = false;
    if (data.arcadeTool === undefined && typeof data.toolName === "string") {
      patch.arcadeTool = data.toolName;
      patch.toolName = undefined;
      dirty = true;
    }
    if (data.arcadeInput === undefined && typeof data.args === "string") {
      const { value } = parseInput(data.args);
      if (value) {
        patch.arcadeInput = value;
        patch.args = undefined;
        dirty = true;
      }
    }
    if (dirty) onChange(patch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentNodeId]);

  // Local textarea buffer so invalid JSON is never silently pushed into
  // arcadeInput (typed as dict[str, Any] on the backend) nor silently
  // discarded from the textarea.
  const [argsText, setArgsText] = useState(() => stringifyInput(data.arcadeInput));
  const [argsError, setArgsError] = useState<string | null>(null);

  // Re-sync when a different node is selected — this panel isn't
  // remounted on selection change (property-panel.tsx renders it without
  // a `key`), so without this the args textarea would keep showing the
  // previously selected node's content.
  useEffect(() => {
    setArgsText(stringifyInput(data.arcadeInput));
    setArgsError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentNodeId]);

  function handleArgsChange(text: string) {
    setArgsText(text);
    const { value, error } = parseInput(text);
    if (error) {
      setArgsError(error);
      return;
    }
    setArgsError(null);
    onChange({ arcadeInput: value });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="arcade-tool">Tool name</Label>
        <Input
          id="arcade-tool"
          value={(data.arcadeTool as string) ?? ""}
          onChange={(e) => onChange({ arcadeTool: e.target.value })}
          placeholder="Google.ListEmails"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="arcade-args">Args (JSON object)</Label>
        <Textarea
          id="arcade-args"
          value={argsText}
          onChange={(e) => handleArgsChange(e.target.value)}
          rows={4}
          placeholder='{"n_emails": 5}'
          className="font-mono text-xs"
          aria-invalid={argsError !== null}
        />
        {argsError && <p className="text-xs text-destructive">{argsError}</p>}
      </div>

      <div className="space-y-2">
        <Label htmlFor="arcade-user-id">User ID</Label>
        <Input
          id="arcade-user-id"
          value={(data.arcadeUserId as string) ?? ""}
          onChange={(e) => onChange({ arcadeUserId: e.target.value })}
          placeholder="{{input.user_email}} or a literal identity"
        />
        <p className="text-[10px] text-muted-foreground">
          Identifies which end user&apos;s Arcade OAuth connection to use. Supports{" "}
          <code className="font-mono">{"{{variable}}"}</code> templating, same as Args —
          use this to give each user their own authorization instead of sharing one
          identity across everyone running this workflow.
        </p>
      </div>

      <p className="text-[10px] text-muted-foreground">
        Requires <code className="font-mono">ARCADE_API_KEY</code> to be configured on the
        backend. On first use of a tool, the workflow will pause and wait for the user to
        authorize it via Arcade&apos;s OAuth flow before continuing.
      </p>
    </div>
  );
}
