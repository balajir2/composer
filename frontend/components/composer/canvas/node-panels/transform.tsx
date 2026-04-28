"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

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
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const transformScript = (data.transformScript as string) ?? "";
  const outputKey = (data.outputKey as string) ?? "";
  const outputKeyError = describeOutputKeyError(outputKey);

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
    </div>
  );
}
