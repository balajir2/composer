import { z } from "zod";

type StartField = {
  name: string;
  label: string;
  type: "text" | "number" | "json" | "boolean";
  required: boolean;
  default?: unknown;
};

type Workflow = {
  nodes: Array<{ type: string; data: Record<string, unknown> }>;
};

type RawVariable = {
  name?: unknown;
  label?: unknown;
  description?: unknown;
  type?: unknown;
  required?: unknown;
  default?: unknown;
  defaultValue?: unknown;
};

function normalizeField(raw: RawVariable): StartField {
  const rawType = String(raw.type ?? "text");
  const type: StartField["type"] = (
    ["text", "number", "boolean", "json"] as const
  ).includes(rawType as StartField["type"])
    ? (rawType as StartField["type"])
    : "text";
  const name = String(raw.name ?? "");
  // Prefer description (backend/canvas field); fall back to label (legacy).
  const label =
    (typeof raw.description === "string" && raw.description) ||
    (typeof raw.label === "string" && raw.label) ||
    name;
  return {
    name,
    label,
    type,
    required: Boolean(raw.required),
    default: raw.defaultValue ?? raw.default,
  };
}

/**
 * Read the Start node's declared inputs and return both the
 * field spec (for rendering) and a zod schema (for validation).
 * Workflows without explicit Start-node field declarations default
 * to a single free-form JSON text field.
 */
export function startNodeSpec(wf: Workflow): { fields: StartField[]; schema: z.ZodTypeAny } {
  const startNode = wf.nodes.find((n) => n.type === "start");
  // Prefer the canonical `inputVariables` key.  Fall back to legacy `inputs`
  // so workflows saved by older builds still render correctly.
  const raw =
    (startNode?.data?.inputVariables as RawVariable[] | undefined) ??
    (startNode?.data?.inputs as RawVariable[] | undefined) ??
    null;
  const declared = raw?.map(normalizeField).filter((f) => f.name);

  if (!declared || declared.length === 0) {
    return {
      fields: [
        {
          name: "input",
          label: "Input (JSON)",
          type: "json",
          required: false,
        },
      ],
      schema: z.object({ input: z.string().optional() }),
    };
  }

  const shape: Record<string, z.ZodTypeAny> = {};
  for (const f of declared) {
    let field: z.ZodTypeAny;
    switch (f.type) {
      case "text":
        field = f.required ? z.string().min(1, "required") : z.string().optional();
        break;
      case "number":
        field = z.coerce.number();
        if (!f.required) field = field.optional();
        break;
      case "boolean":
        field = z.boolean();
        if (!f.required) field = field.optional();
        break;
      case "json":
      default: {
        const jsonField = z.string().refine(
          (v) => {
            if (!v) return !f.required;
            try {
              JSON.parse(v);
              return true;
            } catch {
              return false;
            }
          },
          { message: "must be valid JSON" }
        );
        field = f.required ? jsonField : jsonField.optional();
        break;
      }
    }
    shape[f.name] = field;
  }
  return { fields: declared, schema: z.object(shape) };
}
