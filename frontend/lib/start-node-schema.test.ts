import { describe, it, expect } from "vitest";
import { startNodeSpec } from "./start-node-schema";

function wf(inputVariables: unknown[]) {
  return { nodes: [{ type: "start", data: { inputVariables } }] };
}

describe("startNodeSpec — date/datetime field types", () => {
  it("passes through a date field with its declared type", () => {
    const { fields } = startNodeSpec(wf([{ name: "report_date", type: "date", required: true }]));
    expect(fields[0]?.type).toBe("date");
  });

  it("passes through a datetime field with its declared type", () => {
    const { fields } = startNodeSpec(
      wf([{ name: "extract_timestamp", type: "datetime", required: true }])
    );
    expect(fields[0]?.type).toBe("datetime");
  });

  it("validates a required date field as a non-empty string", () => {
    const { schema } = startNodeSpec(wf([{ name: "report_date", type: "date", required: true }]));
    expect(schema.safeParse({ report_date: "" }).success).toBe(false);
    expect(schema.safeParse({ report_date: "2026-07-20" }).success).toBe(true);
  });

  it("validates an optional datetime field", () => {
    const { schema } = startNodeSpec(
      wf([{ name: "extract_timestamp", type: "datetime", required: false }])
    );
    expect(schema.safeParse({}).success).toBe(true);
    expect(schema.safeParse({ extract_timestamp: "2026-07-20T14:30:00" }).success).toBe(true);
  });
});
