"use client";

import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

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
        <Label>Classifier type</Label>
        <Select
          value={(data.classifierType as string) ?? ""}
          onValueChange={(v) => onChange({ classifierType: v })}
        >
          <SelectTrigger>
            <SelectValue placeholder="Select classifier" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="pii">PII</SelectItem>
            <SelectItem value="moderation">Moderation</SelectItem>
            <SelectItem value="jailbreak">Jailbreak</SelectItem>
            <SelectItem value="hallucination">Hallucination</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>On failure</Label>
        <Select
          value={(data.onFail as string) ?? "fail"}
          onValueChange={(v) => onChange({ onFail: v })}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="fail">Fail execution</SelectItem>
            <SelectItem value="continue">Continue</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}
