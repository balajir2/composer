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

/**
 * Read the Start node's declared inputs (if any) and return both the
 * field spec (for rendering) and a zod schema (for validation).
 * Workflows without explicit Start-node field declarations default
 * to a single free-form JSON text field.
 */
export function startNodeSpec(wf: Workflow): { fields: StartField[]; schema: z.ZodTypeAny } {
  const startNode = wf.nodes.find((n) => n.type === "start");
  const declared = (startNode?.data?.inputs as StartField[] | undefined) ?? null;

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
