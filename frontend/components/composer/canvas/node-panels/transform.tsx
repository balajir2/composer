"use client";

import { useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { evaluateTransformExpression } from "@/lib/api/expressions";
import { SampleStateField, useSampleState } from "./sample-state-field";

/**
 * Transform node panel.
 *
 * Field names match `TransformNodeData` in src/engine/workflow.py:
 *   transformScript : the simpleeval expression
 *   outputKey       : optional named state variable to write the result
 *                     to (in addition to `lastOutput`)
 *
 * The previous panel saved `inputVariable` + `expression`, neither of
 * which the executor reads — it only reads `transformScript`.  That
 * means every transform node saved through the old panel ran with no
 * script and 422'd.  Re-open existing transform nodes through this
 * rebuilt panel and re-save.
 */

const RESERVED = new Set(["variables", "lastOutput", "node_results"]);
const IDENT = /^[A-Za-z_][A-Za-z0-9_]*$/;

function describeOutputKeyError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null; // empty = unset, valid
  if (!IDENT.test(trimmed))
    return "Must be a valid identifier (letters, digits, underscore; can't start with a digit).";
  if (trimmed.startsWith("_"))
    return "Names starting with '_' are reserved for engine internals.";
  if (RESERVED.has(trimmed))
    return `'${trimmed}' is reserved — it's a built-in scope alias.`;
  return null;
}

export default function TransformPanel({
  data,
  onChange,
  workflowId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  workflowId?: string;
}) {
  const transformScript = (data.transformScript as string) ?? "";
  const outputKey = (data.outputKey as string) ?? "";
  const outputKeyError = describeOutputKeyError(outputKey);

  const sampleState = useSampleState(workflowId);
  const testMutation = useMutation({
    mutationFn: () =>
      evaluateTransformExpression({
        expression: transformScript,
        variables: sampleState.parsed ?? {},
      }),
  });
  const { reset: resetTest } = testMutation;

  // A stale result from a previous expression/sample-state combination is
  // actively misleading once either changes -- the whole point of this
  // section is iterate-then-test, so the result must not outlive the input
  // it was computed from.
  useEffect(() => {
    resetTest();
  }, [transformScript, sampleState.text, resetTest]);

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="transform-script">Expression</Label>
        <Textarea
          id="transform-script"
          value={transformScript}
          onChange={(e) => onChange({ transformScript: e.target.value })}
          rows={5}
          placeholder="lastOutput.upper()"
          className="font-mono text-xs"
        />
        <p className="text-xs text-muted-foreground">
          A simpleeval expression. Read state via{" "}
          <code className="font-mono">variables[&quot;x&quot;]</code>,{" "}
          <code className="font-mono">lastOutput</code>, top-level
          variable names directly (e.g.{" "}
          <code className="font-mono">counter</code>), or Mustache style{" "}
          <code className="font-mono">&#123;&#123;path.to.field&#125;&#125;</code>.
          Functions: <code className="font-mono">int</code>,{" "}
          <code className="font-mono">float</code>,{" "}
          <code className="font-mono">str</code>,{" "}
          <code className="font-mono">bool</code>,{" "}
          <code className="font-mono">len</code>.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="transform-output-key">
          Output variable name{" "}
          <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Input
          id="transform-output-key"
          value={outputKey}
          onChange={(e) =>
            onChange({ outputKey: e.target.value || undefined })
          }
          placeholder="counter"
          aria-invalid={outputKeyError !== null}
          className="font-mono text-xs"
        />
        {outputKeyError ? (
          <p className="text-xs text-destructive">{outputKeyError}</p>
        ) : (
          <p className="text-xs text-muted-foreground">
            Save the result to a named state variable in addition to{" "}
            <code className="font-mono">lastOutput</code>. Lets a single
            transform serve as both compute and persist — essential for
            loop counters, accumulators, and any pipeline where multiple
            transforms run in sequence without clobbering each other.
            Reference downstream as{" "}
            <code className="font-mono">
              &#123;&#123;{outputKey || "name"}&#125;&#125;
            </code>{" "}
            or directly as{" "}
            <code className="font-mono">{outputKey || "name"}</code> in
            another expression.
          </p>
        )}
      </div>

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
          disabled={!transformScript.trim() || sampleState.error !== null || testMutation.isPending}
          onClick={() => testMutation.mutate()}
        >
          {testMutation.isPending ? "Testing…" : "Test"}
        </Button>
        {testMutation.data &&
          (testMutation.data.ok ? (
            <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">
              {typeof testMutation.data.result === "string"
                ? testMutation.data.result
                : JSON.stringify(testMutation.data.result, null, 2)}
            </pre>
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
