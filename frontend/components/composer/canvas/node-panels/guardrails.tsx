"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

/**
 * Guardrails node panel.
 *
 * Field names match `GuardrailsNodeData` (src/engine/workflow.py:297).
 * The previous panel saved `classifierType` (single value) and `onFail`
 * (`fail`/`continue`) — neither key was read by the executor, so any
 * existing guardrails node was effectively a no-op (all four checks
 * disabled by default).  Existing nodes need re-saving through this
 * rebuilt panel to actually classify anything.
 */

const ACTION_OPTIONS = [
  // Block = raise GuardrailViolationError → halts the workflow.
  // Warn  = pass through, surface the verdict on the node output for
  //         downstream `if-else` to branch on.
  { value: "block", label: "Block — fail the run on any violation" },
  { value: "warn", label: "Warn — keep running, expose the verdict downstream" },
];

/**
 * Snake_case the user-given node name to match the events_wrapper alias.
 * Mirror of _sanitize_node_name in src/engine/events_wrapper.py — keep
 * the two in lock-step or `{{<alias>.passed}}` won't resolve.
 */
function sanitizeNodeName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export default function GuardrailsPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const piiEnabled = (data.piiEnabled as boolean | undefined) ?? false;
  const moderationEnabled =
    (data.moderationEnabled as boolean | undefined) ?? false;
  const jailbreakEnabled =
    (data.jailbreakEnabled as boolean | undefined) ?? false;
  const hallucinationEnabled =
    (data.hallucinationEnabled as boolean | undefined) ?? false;
  const actionOnViolation =
    (data.actionOnViolation as string | undefined) ?? "warn";
  const model = (data.model as string | undefined) ?? "";

  // The events_wrapper exposes this node's output under the snake_case
  // of the user-given Name field.  Compute it here so the doc block
  // shows the EXACT string the designer should paste — angle-bracket
  // placeholders like `<name>` lured users into copying them verbatim.
  const rawNodeName =
    (data.nodeName as string | undefined) ??
    (data.label as string | undefined) ??
    "";
  const aliasFromName = sanitizeNodeName(rawNodeName);
  const alias = aliasFromName || "your_node_name";

  const anyChecked =
    piiEnabled || moderationEnabled || jailbreakEnabled || hallucinationEnabled;

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Checks</Label>
        <div className="space-y-1 rounded-md border p-2">
          <Toggle
            label="PII"
            description="Names, emails, phones, addresses, SSN, credit cards, gov IDs"
            checked={piiEnabled}
            onChange={(v) => onChange({ piiEnabled: v })}
          />
          <Toggle
            label="Moderation"
            description="Harmful, abusive, violent, sexual, or hateful content"
            checked={moderationEnabled}
            onChange={(v) => onChange({ moderationEnabled: v })}
          />
          <Toggle
            label="Jailbreak"
            description="Prompt-injection attempts, role-play escapes, system overrides"
            checked={jailbreakEnabled}
            onChange={(v) => onChange({ jailbreakEnabled: v })}
          />
          <Toggle
            label="Hallucination"
            description="Claims that look factually wrong, contradictory, or unverifiable"
            checked={hallucinationEnabled}
            onChange={(v) => onChange({ hallucinationEnabled: v })}
          />
        </div>
        {!anyChecked && (
          <p className="text-xs text-amber-700">
            No checks enabled — the node will pass everything through.
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="guard-action">On violation</Label>
        <NativeSelect
          id="guard-action"
          value={actionOnViolation}
          onValueChange={(v) => onChange({ actionOnViolation: v })}
          options={ACTION_OPTIONS}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="guard-model">
          Classifier model{" "}
          <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Input
          id="guard-model"
          value={model}
          onChange={(e) => onChange({ model: e.target.value || undefined })}
          placeholder="anthropic/claude-haiku-4-5-20251001 (default)"
        />
        <p className="text-xs text-muted-foreground">
          Leave blank to use the default Haiku 4.5 classifier (cheap, fast,
          one 10-token call per enabled check, run concurrently).
        </p>
      </div>

      {/* Pass-through documentation — answers the "where does my upstream
          content go?" question that comes up immediately.  Fields show
          the LIVE alias for THIS node so designers can copy verbatim;
          earlier `<name>` placeholder text was a copy-paste trap. */}
      <div className="space-y-2 rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        <p className="font-medium text-foreground">Output behaviour</p>
        <p>
          The upstream node&apos;s <code className="font-mono">lastOutput</code>{" "}
          is preserved unchanged — guardrails sits transparently between
          steps. The verdict is exposed on this node&apos;s own alias.
        </p>
        {!aliasFromName && (
          <p className="rounded bg-amber-50 px-2 py-1 text-amber-900">
            Set this node&apos;s <strong>Name</strong> field above first.
            The samples below will show the real alias once it&apos;s set.
          </p>
        )}
        <p className="text-foreground">
          Reference this node in downstream prompts and conditions as:
        </p>
        <ul className="space-y-1">
          <li>
            <CopyableCode value={`{{${alias}.passed}}`} /> →
            boolean (true / false)
          </li>
          <li>
            <CopyableCode value={`{{${alias}.violations}}`} /> →
            array, e.g. <code className="font-mono">[&quot;PII detected&quot;]</code>
          </li>
          <li>
            <CopyableCode value={`{{${alias}.message}}`} /> →
            human-readable summary
          </li>
        </ul>
        <p>
          For an <strong>If/Else</strong> node after this one (Warn mode),
          paste this exactly into the condition field — no quotes, no
          extra text:
        </p>
        <CopyableCode value={`{{${alias}.passed}}`} block />
      </div>
    </div>
  );
}

/**
 * Renders a code expression that the user can click to copy verbatim.
 * Solves the copy-paste trap where designers grabbed surrounding prose
 * (e.g. " — boolean") along with the expression and got a SyntaxError
 * downstream.
 */
function CopyableCode({
  value,
  block = false,
}: {
  value: string;
  /** When true, render as a full-width block instead of inline. */
  block?: boolean;
}) {
  function handleClick() {
    void navigator.clipboard.writeText(value).catch(() => {
      /* clipboard blocked — silently no-op; users can also select+copy */
    });
  }
  if (block) {
    return (
      <button
        type="button"
        onClick={handleClick}
        title="Click to copy"
        className="block w-full cursor-pointer select-all rounded border bg-white px-2 py-1 text-left font-mono text-xs text-foreground hover:bg-muted"
      >
        {value}
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      title="Click to copy"
      className="cursor-pointer select-all rounded border bg-white px-1.5 py-0.5 font-mono text-[11px] text-foreground hover:bg-muted"
    >
      {value}
    </button>
  );
}

function Toggle({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded px-1.5 py-1 text-sm hover:bg-muted/50">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0"
      />
      <span className="flex-1">
        <span className="font-medium">{label}</span>
        <span className="block text-xs text-muted-foreground">{description}</span>
      </span>
    </label>
  );
}
