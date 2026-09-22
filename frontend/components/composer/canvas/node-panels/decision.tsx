"use client";

import { useQuery } from "@tanstack/react-query";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { Plus, Trash2 } from "lucide-react";
import { listEnabledLlmModels } from "@/lib/api/llm-models";

type Mode = "binary" | "choice";

type DecisionExample = { input: string; result?: boolean; option?: string };
type DecisionOption = { label: string; description?: string };

// This panel's own two-option provider list ("LLM" / "TypeSafe (Jev)") is
// the only place TypeSafe appears in any workflow-facing panel — do not
// hoist this into a shared PROVIDER_OPTIONS used by other node panels
// (see agent.tsx's PROVIDER_OPTIONS, which is intentionally separate).
const PROVIDER_OPTIONS = [
  { value: "llm", label: "LLM" },
  { value: "typesafe", label: "TypeSafe (Jev)" },
];

const MODE_OPTIONS = [
  { value: "binary", label: "Binary (yes/no)" },
  { value: "choice", label: "Choice (pick one)" },
];

function readExamples(data: Record<string, unknown>): DecisionExample[] {
  return Array.isArray(data.examples) ? (data.examples as DecisionExample[]) : [];
}

function readOptions(data: Record<string, unknown>): DecisionOption[] {
  return Array.isArray(data.options) ? (data.options as DecisionOption[]) : [];
}

export default function DecisionPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const mode = ((data.mode as Mode) ?? "binary") as Mode;
  const provider = (data.provider as string) ?? "llm";
  const examples = readExamples(data);
  const options = readOptions(data);

  const { data: models = [] } = useQuery({
    queryKey: ["llm-models", provider],
    queryFn: () => listEnabledLlmModels(provider),
  });

  function addExample() {
    onChange({
      examples: [
        ...examples,
        mode === "binary" ? { input: "", result: false } : { input: "", option: "" },
      ],
    });
  }

  function updateExample(index: number, patch: Partial<DecisionExample>) {
    onChange({ examples: examples.map((e, i) => (i === index ? { ...e, ...patch } : e)) });
  }

  function removeExample(index: number) {
    onChange({ examples: examples.filter((_, i) => i !== index) });
  }

  function addOption() {
    onChange({ options: [...options, { label: "", description: "" }] });
  }

  function updateOption(index: number, patch: Partial<DecisionOption>) {
    onChange({ options: options.map((o, i) => (i === index ? { ...o, ...patch } : o)) });
  }

  function removeOption(index: number) {
    onChange({ options: options.filter((_, i) => i !== index) });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="decision-mode">Mode</Label>
        <NativeSelect
          id="decision-mode"
          aria-label="Mode"
          value={mode}
          onValueChange={(v) => onChange({ mode: v })}
          options={MODE_OPTIONS}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="decision-provider">Provider</Label>
        <NativeSelect
          id="decision-provider"
          aria-label="Provider"
          value={provider}
          onValueChange={(v) => onChange({ provider: v })}
          options={PROVIDER_OPTIONS}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="decision-instruction">Instruction</Label>
        <Textarea
          id="decision-instruction"
          value={(data.instruction as string) ?? ""}
          onChange={(e) => onChange({ instruction: e.target.value })}
          placeholder="Is this a refund request?"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="decision-model">Model</Label>
        <NativeSelect
          id="decision-model"
          aria-label="Model"
          value={(data.model as string) ?? ""}
          onValueChange={(v) => {
            const written = provider === "typesafe" ? v : `${provider}/${v}`;
            onChange({ model: written });
          }}
          options={models.map((m) => ({ value: m.modelId, label: m.label ?? m.modelId }))}
        />
      </div>

      <div className="space-y-2">
        <Label className="text-xs font-semibold uppercase text-muted-foreground">Examples</Label>
        {provider === "typesafe" && (
          <p className="text-xs text-muted-foreground">
            TypeSafe has no native few-shot mechanism — examples are folded into criteria text as
            illustrative guidance, not true few-shot.
          </p>
        )}
        {examples.length === 0 && (
          <p className="text-xs text-muted-foreground">
            Zero-shot decisions can be inconsistent on edge cases. Consider adding 1-2 examples.
          </p>
        )}
        {examples.map((ex, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input
              value={ex.input}
              onChange={(e) => updateExample(i, { input: e.target.value })}
              placeholder="Example text"
              className="h-7 text-xs"
            />
            {mode === "binary" ? (
              <NativeSelect
                value={String(ex.result ?? false)}
                onValueChange={(v) => updateExample(i, { result: v === "true" })}
                options={[
                  { value: "true", label: "true" },
                  { value: "false", label: "false" },
                ]}
              />
            ) : (
              <Input
                value={ex.option ?? ""}
                onChange={(e) => updateExample(i, { option: e.target.value })}
                placeholder="option label"
                className="h-7 text-xs"
              />
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => removeExample(i)}
              aria-label={`Remove example ${i + 1}`}
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        ))}
        <Button variant="outline" size="sm" className="w-full text-xs" onClick={addExample}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add example
        </Button>
      </div>

      {mode === "choice" && (
        <div className="space-y-2">
          <Label className="text-xs font-semibold uppercase text-muted-foreground">Options</Label>
          {options.map((opt, i) => (
            <div key={i} className="flex items-center gap-2">
              <Input
                value={opt.label}
                onChange={(e) => updateOption(i, { label: e.target.value })}
                placeholder="Label"
                className="h-7 text-xs"
              />
              <Input
                value={opt.description ?? ""}
                onChange={(e) => updateOption(i, { description: e.target.value })}
                placeholder="Description (optional)"
                className="h-7 text-xs"
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() => removeOption(i)}
                aria-label={`Remove option ${i + 1}`}
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
          ))}
          <Button variant="outline" size="sm" className="w-full text-xs" onClick={addOption}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add option
          </Button>
        </div>
      )}
    </div>
  );
}
