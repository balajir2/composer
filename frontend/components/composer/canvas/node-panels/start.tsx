"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Plus, Trash2 } from "lucide-react";

type InputField = {
  name: string;
  label: string;
  type: string;
  required: boolean;
};

export default function StartPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const inputs: InputField[] = Array.isArray(data.inputs) ? (data.inputs as InputField[]) : [];

  function updateField(index: number, patch: Partial<InputField>) {
    const updated = inputs.map((f, i) => (i === index ? { ...f, ...patch } : f));
    onChange({ inputs: updated });
  }

  function addField() {
    onChange({
      inputs: [...inputs, { name: "", label: "", type: "string", required: false }],
    });
  }

  function removeField(index: number) {
    onChange({ inputs: inputs.filter((_, i) => i !== index) });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label className="text-xs font-semibold uppercase text-muted-foreground">
          Input fields
        </Label>
        {inputs.map((field, i) => (
          <div key={i} className="space-y-1.5 rounded-md border p-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">Field {i + 1}</span>
              <Button
                variant="ghost"
                size="icon"
                className="h-5 w-5"
                onClick={() => removeField(i)}
                aria-label={`Remove field ${i + 1}`}
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Name</Label>
              <Input
                value={field.name}
                onChange={(e) => updateField(i, { name: e.target.value })}
                placeholder="field_name"
                className="h-7 text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Label</Label>
              <Input
                value={field.label}
                onChange={(e) => updateField(i, { label: e.target.value })}
                placeholder="Display label"
                className="h-7 text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Type</Label>
              <Select
                value={field.type}
                onValueChange={(v) => updateField(i, { type: v ?? "string" })}
              >
                <SelectTrigger className="h-7 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="string">string</SelectItem>
                  <SelectItem value="number">number</SelectItem>
                  <SelectItem value="boolean">boolean</SelectItem>
                  <SelectItem value="array">array</SelectItem>
                  <SelectItem value="object">object</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id={`req-${i}`}
                checked={field.required}
                onChange={(e) => updateField(i, { required: e.target.checked })}
                className="h-3.5 w-3.5"
              />
              <Label htmlFor={`req-${i}`} className="text-xs">
                Required
              </Label>
            </div>
          </div>
        ))}
        <Button variant="outline" size="sm" className="w-full text-xs" onClick={addField}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add field
        </Button>
      </div>
    </div>
  );
}
