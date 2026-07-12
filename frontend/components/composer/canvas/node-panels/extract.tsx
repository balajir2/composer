"use client";

import { useEffect, useState } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

function stringifySchema(schema: unknown): string {
  if (schema && typeof schema === "object" && Object.keys(schema).length > 0) {
    return JSON.stringify(schema, null, 2);
  }
  return "";
}

function parseSchema(text: string): { value?: Record<string, unknown> | null; error?: string } {
  if (!text.trim()) return { value: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: "Invalid JSON." };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { error: "Output schema must be a JSON object." };
  }
  return { value: parsed as Record<string, unknown> };
}

export default function ExtractPanel({
  data,
  onChange,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  // Legacy-field migration (P0-0): the Designer used to write
  // inputVariable/schema instead of the canonical input/jsonSchema
  // aliases ExtractNodeData actually reads.
  useEffect(() => {
    const patch: Record<string, unknown> = {};
    let dirty = false;
    if (data.input === undefined && typeof data.inputVariable === "string") {
      patch.input = data.inputVariable;
      patch.inputVariable = undefined;
      dirty = true;
    }
    if (data.jsonSchema === undefined && typeof data.schema === "string") {
      const { value } = parseSchema(data.schema);
      if (value) {
        patch.jsonSchema = value;
        patch.schema = undefined;
        dirty = true;
      }
    }
    if (dirty) onChange(patch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentNodeId]);

  const [schemaText, setSchemaText] = useState(() => stringifySchema(data.jsonSchema));
  const [schemaError, setSchemaError] = useState<string | null>(null);

  useEffect(() => {
    setSchemaText(stringifySchema(data.jsonSchema));
    setSchemaError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentNodeId]);

  function handleSchemaChange(text: string) {
    setSchemaText(text);
    const { value, error } = parseSchema(text);
    if (error) {
      setSchemaError(error);
      return;
    }
    setSchemaError(null);
    onChange({ jsonSchema: value });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="extract-input">Input</Label>
        <Input
          id="extract-input"
          value={(data.input as string) ?? ""}
          onChange={(e) => onChange({ input: e.target.value })}
          placeholder="{{lastOutput}} (default when left blank)"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="extract-schema">Output schema (JSON, optional)</Label>
        <Textarea
          id="extract-schema"
          value={schemaText}
          onChange={(e) => handleSchemaChange(e.target.value)}
          rows={6}
          placeholder='{"name": "string", "age": "number"}'
          className="font-mono text-xs"
          aria-invalid={schemaError !== null}
        />
        {schemaError && <p className="text-xs text-destructive">{schemaError}</p>}
        <p className="text-[10px] text-muted-foreground">
          JSON schema describing the fields to extract. Leave blank to let the model
          return JSON without a fixed shape.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="extract-model">Model (optional)</Label>
        <Input
          id="extract-model"
          value={(data.model as string) ?? ""}
          onChange={(e) => onChange({ model: e.target.value })}
          placeholder="anthropic/claude-haiku-4-5-20251001 (default when left blank)"
        />
      </div>
    </div>
  );
}
