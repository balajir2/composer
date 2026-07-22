"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { listExecutions } from "@/lib/api/executions";

export interface SampleState {
  text: string;
  setText: (text: string) => void;
  parsed: Record<string, unknown> | null;
  error: string | null;
}

/**
 * Sample-state text, pre-filled once from the workflow's most recent
 * execution's variables (if any exist), then fully editable. Parses on
 * every change so callers can gate their "Test" button on validity.
 */
export function useSampleState(workflowId?: string): SampleState {
  const [text, setText] = useState("{}");
  const prefilled = useRef(false);

  const { data } = useQuery({
    queryKey: ["latest-execution-variables", workflowId],
    queryFn: () => listExecutions({ workflowId, limit: 1 }),
    enabled: Boolean(workflowId) && !prefilled.current,
  });

  useEffect(() => {
    if (prefilled.current || !data) return;
    prefilled.current = true;
    const latest = data.items[0];
    const variables = latest?.variables as Record<string, unknown> | undefined;
    if (variables && Object.keys(variables).length > 0) {
      setText(JSON.stringify(variables, null, 2));
    }
  }, [data]);

  let parsed: Record<string, unknown> | null = null;
  let error: string | null = null;
  try {
    const value: unknown = JSON.parse(text);
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      parsed = value as Record<string, unknown>;
    } else {
      error = "Sample state must be a JSON object";
    }
  } catch {
    error = "Invalid JSON";
  }

  return { text, setText, parsed, error };
}

export function SampleStateField({
  text,
  onChangeText,
  error,
}: {
  text: string;
  onChangeText: (text: string) => void;
  error: string | null;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor="sample-state">Sample state (variables)</Label>
      <Textarea
        id="sample-state"
        value={text}
        onChange={(e) => onChangeText(e.target.value)}
        rows={4}
        className="font-mono text-xs"
        aria-invalid={error !== null}
      />
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Pre-filled from the workflow&apos;s most recent execution, if any. Edit
          freely before testing.
        </p>
      )}
    </div>
  );
}
