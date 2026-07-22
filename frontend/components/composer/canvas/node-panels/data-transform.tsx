"use client";

import { useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { evaluateDataTransformExpression } from "@/lib/api/expressions";
import { SampleStateField, useSampleState } from "./sample-state-field";

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
  workflowId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  currentNodeId?: string;
  workflowId?: string;
}) {
  const operation = (data.operation as string) ?? "map";
  const collection = (data.collection as string) ?? "";
  const expression = (data.expression as string) ?? "";
  const itemVar = (data.itemVar as string) ?? "item";

  const sampleState = useSampleState(workflowId);
  const testMutation = useMutation({
    mutationFn: () =>
      evaluateDataTransformExpression({
        operation,
        collection,
        expression,
        itemVar,
        initial: data.initial,
        variables: sampleState.parsed ?? {},
      }),
  });
  const { reset: resetTest } = testMutation;

  // A stale result from a previous operation/collection/expression/sample
  // combination is actively misleading once any of them change -- the
  // whole point of this section is iterate-then-test, so the result must
  // not outlive the input it was computed from. (Lesson from the sibling
  // transform.tsx panel's own Test-expression section, fixed there after
  // code review caught the same gap.)
  useEffect(() => {
    resetTest();
  }, [operation, collection, expression, itemVar, data.initial, sampleState.text, resetTest]);

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

      <div className="space-y-2 border-t pt-4">
        <Label>Test expression</Label>
        <SampleStateField
          text={sampleState.text}
          onChangeText={sampleState.setText}
          error={sampleState.error}
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={
            !collection.trim() ||
            !expression.trim() ||
            sampleState.error !== null ||
            testMutation.isPending
          }
          onClick={() => testMutation.mutate()}
        >
          {testMutation.isPending ? "Testing…" : "Test"}
        </Button>
        {testMutation.data &&
          (testMutation.data.ok ? (
            <div className="space-y-1">
              {testMutation.data.truncated && (
                <p className="text-xs text-amber-600">
                  Tested against the first {testMutation.data.itemCount} items — the
                  full collection has more.
                </p>
              )}
              <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">
                {typeof testMutation.data.result === "string"
                  ? testMutation.data.result
                  : JSON.stringify(testMutation.data.result, null, 2)}
              </pre>
            </div>
          ) : (
            <p className="text-xs text-destructive">{testMutation.data.error}</p>
          ))}
        {testMutation.isError && (
          <p className="text-xs text-destructive">
            {testMutation.error instanceof Error ? testMutation.error.message : "Test failed."}
          </p>
        )}
      </div>
    </div>
  );
}
