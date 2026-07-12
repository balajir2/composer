"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";

const OPERATION_OPTIONS = [
  { value: "map", label: "Map" },
  { value: "filter", label: "Filter" },
  { value: "reduce", label: "Reduce" },
];

function parseInitial(text: string): unknown {
  if (!text.trim()) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function stringifyInitial(initial: unknown): string {
  if (initial === undefined || initial === null) return "";
  if (typeof initial === "string") return initial;
  try {
    return JSON.stringify(initial);
  } catch {
    return "";
  }
}

export default function DataTransformPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
}) {
  const operation = (data.operation as string) ?? "map";

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="data-transform-operation">Operation</Label>
        <NativeSelect
          id="data-transform-operation"
          value={operation}
          onValueChange={(v) => onChange({ operation: v })}
          options={OPERATION_OPTIONS}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="data-transform-collection">Collection expression</Label>
        <Input
          id="data-transform-collection"
          value={(data.collection as string) ?? ""}
          onChange={(e) => onChange({ collection: e.target.value })}
          placeholder="lastOutput.items"
        />
        <p className="text-[10px] text-muted-foreground">
          A simpleeval expression that evaluates to the list this node iterates over.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="data-transform-expression">Per-item expression</Label>
        <Textarea
          id="data-transform-expression"
          value={(data.expression as string) ?? ""}
          onChange={(e) => onChange({ expression: e.target.value })}
          rows={4}
          placeholder={
            operation === "filter" ? "item.active" : operation === "reduce" ? "acc + item.value" : "item.name"
          }
          className="font-mono text-xs"
        />
        <p className="text-xs text-muted-foreground">
          A simpleeval expression, evaluated once per item — plain Python-like syntax, not
          a templating language.
          {operation === "map" && " Its result becomes that item's output."}
          {operation === "filter" && " Items where this evaluates truthy are kept."}
          {operation === "reduce" &&
            " Combines the running accumulator (see item variable name below) with each item."}
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="data-transform-item-var">Item variable name</Label>
        <Input
          id="data-transform-item-var"
          value={(data.itemVar as string) ?? "item"}
          onChange={(e) => onChange({ itemVar: e.target.value })}
          placeholder="item"
        />
        <p className="text-[10px] text-muted-foreground">
          {operation === "reduce"
            ? "For reduce, this name refers to the running accumulator inside the expression."
            : "The name the per-item expression uses to refer to the current item."}
        </p>
      </div>

      {operation === "reduce" && (
        <div className="space-y-2">
          <Label htmlFor="data-transform-initial">Initial value (JSON or raw text)</Label>
          <Input
            id="data-transform-initial"
            defaultValue={stringifyInitial(data.initial)}
            onChange={(e) => onChange({ initial: parseInitial(e.target.value) })}
            placeholder="0 or [] or {}"
          />
        </div>
      )}
    </div>
  );
}
