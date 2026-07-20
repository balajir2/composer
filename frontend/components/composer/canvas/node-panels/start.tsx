"use client";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/native-select";
import { Plus, Trash2 } from "lucide-react";
import { DatePickerButton, DateTimePickerButton } from "@/components/composer/date-field";

// Mirrors backend StartInputVariable (src/engine/workflow.py).
// type values are what the End-User input form + runtime renderer understand:
// "text" → plain string, "number" → numeric, "boolean" → checkbox,
// "json" → Textarea (accepts any JSON-serializable value),
// "document" → file picker; uploads to /uploads/extract-text and the
//              extracted plain text is what flows downstream as a
//              regular string variable.
// "date" → "YYYY-MM-DD" string, "datetime" → "YYYY-MM-DDTHH:mm:ss" string.
type InputField = {
  name: string;
  type: "text" | "number" | "boolean" | "json" | "document" | "date" | "datetime";
  required: boolean;
  description?: string;
  defaultValue?: unknown;
};

const ALLOWED_TYPES = [
  "text",
  "number",
  "boolean",
  "json",
  "document",
  "date",
  "datetime",
] as const;

const TYPE_OPTIONS = [
  { value: "text", label: "text" },
  { value: "number", label: "number" },
  { value: "boolean", label: "boolean" },
  { value: "json", label: "json (object / array)" },
  { value: "document", label: "document (PDF / DOCX / MD / TXT upload)" },
  { value: "date", label: "date" },
  { value: "datetime", label: "date & time" },
];

function readVariables(data: Record<string, unknown>): InputField[] {
  // Prefer new inputVariables; fall back to legacy inputs saved by older builds.
  const fromNew = data.inputVariables;
  if (Array.isArray(fromNew)) return fromNew as InputField[];
  const legacy = data.inputs;
  if (Array.isArray(legacy)) {
    return (legacy as Array<Record<string, unknown>>).map((f) => ({
      name: String(f.name ?? ""),
      type: ALLOWED_TYPES.includes(String(f.type) as (typeof ALLOWED_TYPES)[number])
        ? (f.type as InputField["type"])
        : "text",
      required: Boolean(f.required),
      description: typeof f.description === "string" ? f.description : undefined,
      defaultValue: f.defaultValue,
    }));
  }
  return [];
}

export default function StartPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const inputVariables = readVariables(data);

  function commit(next: InputField[]) {
    // Drop legacy `inputs` so downstream readers only see one source of truth.
    onChange({ inputVariables: next, inputs: undefined });
  }

  function updateField(index: number, patch: Partial<InputField>) {
    commit(inputVariables.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  }

  function addField() {
    commit([...inputVariables, { name: "", type: "text", required: false, description: "" }]);
  }

  function removeField(index: number) {
    commit(inputVariables.filter((_, i) => i !== index));
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label className="text-xs font-semibold uppercase text-muted-foreground">
          Input variables
        </Label>
        <p className="text-xs text-muted-foreground">
          Declared here become state variables available to every downstream node. Reference as{" "}
          <code>{"{{name}}"}</code> in prompts, URLs, and transforms.
        </p>
        {inputVariables.map((field, i) => (
          <div key={i} className="space-y-1.5 rounded-md border p-2">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">Variable {i + 1}</span>
              <Button
                variant="ghost"
                size="icon"
                className="h-5 w-5"
                onClick={() => removeField(i)}
                aria-label={`Remove variable ${i + 1}`}
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Name</Label>
              <Input
                value={field.name}
                onChange={(e) => updateField(i, { name: e.target.value })}
                placeholder="customer_name"
                className="h-7 text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Description (shown to end-users)</Label>
              <Input
                value={field.description ?? ""}
                onChange={(e) => updateField(i, { description: e.target.value })}
                placeholder="Full legal name of the customer"
                className="h-7 text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Type</Label>
              <NativeSelect
                value={field.type}
                onValueChange={(v) => updateField(i, { type: v as InputField["type"] })}
                options={TYPE_OPTIONS}
                className="h-7 text-xs"
              />
            </div>
            {field.type === "document" ? (
              // No default value for documents — files are always
              // uploaded fresh per run.  Show a one-line note instead
              // so the panel structure stays predictable.
              <p className="text-xs text-muted-foreground">
                End-users get a file picker (PDF / DOCX / MD / TXT, max 10MB). Extracted text flows
                downstream as a regular string variable — reference as{" "}
                <code className="font-mono">&#123;&#123;{field.name || "name"}&#125;&#125;</code>.
              </p>
            ) : field.type === "date" ? (
              <div className="space-y-1">
                <Label className="text-xs">Default value (optional)</Label>
                <DatePickerButton
                  value={
                    field.defaultValue === undefined || field.defaultValue === null
                      ? ""
                      : String(field.defaultValue)
                  }
                  onChange={(v) => updateField(i, { defaultValue: v || undefined })}
                />
              </div>
            ) : field.type === "datetime" ? (
              <div className="space-y-1">
                <Label className="text-xs">Default value (optional)</Label>
                <DateTimePickerButton
                  value={
                    field.defaultValue === undefined || field.defaultValue === null
                      ? ""
                      : String(field.defaultValue)
                  }
                  onChange={(v) => updateField(i, { defaultValue: v || undefined })}
                />
              </div>
            ) : (
              <div className="space-y-1">
                <Label className="text-xs">Default value (optional)</Label>
                <Input
                  value={
                    field.defaultValue === undefined || field.defaultValue === null
                      ? ""
                      : String(field.defaultValue)
                  }
                  onChange={(e) =>
                    updateField(i, {
                      defaultValue: e.target.value === "" ? undefined : e.target.value,
                    })
                  }
                  placeholder={field.type === "json" ? '{"key": "value"}' : "leave blank for none"}
                  className="h-7 text-xs"
                />
              </div>
            )}
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
          Add variable
        </Button>
      </div>
    </div>
  );
}
