"use client";

import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";

const CLASSIFIER_OPTIONS = [
  { value: "pii", label: "PII" },
  { value: "moderation", label: "Moderation" },
  { value: "jailbreak", label: "Jailbreak" },
  { value: "hallucination", label: "Hallucination" },
];

const ON_FAIL_OPTIONS = [
  { value: "fail", label: "Fail execution" },
  { value: "continue", label: "Continue" },
];

export default function GuardrailsPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="guard-classifier">Classifier type</Label>
        <NativeSelect
          id="guard-classifier"
          value={(data.classifierType as string) ?? ""}
          onValueChange={(v) => onChange({ classifierType: v })}
          options={CLASSIFIER_OPTIONS}
          placeholder="Select classifier"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="guard-onfail">On failure</Label>
        <NativeSelect
          id="guard-onfail"
          value={(data.onFail as string) ?? "fail"}
          onValueChange={(v) => onChange({ onFail: v })}
          options={ON_FAIL_OPTIONS}
        />
      </div>
    </div>
  );
}
