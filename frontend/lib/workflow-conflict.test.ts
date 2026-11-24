import { describe, expect, it } from "vitest";
import { workflowChanged } from "./workflow-conflict";

const err = (status: number, detail: unknown) => ({ status, detail: { detail } });

describe("workflowChanged", () => {
  it("recognises the stale-precondition 409 and returns when it changed", () => {
    expect(
      workflowChanged(
        err(409, {
          code: "workflow_changed",
          message: "workflow changed since you loaded it",
          currentUpdatedAt: "2026-11-24T10:05:00.500000Z",
        })
      )
    ).toEqual({ currentUpdatedAt: "2026-11-24T10:05:00.500000Z" });
  });

  it("ignores a slug-clash 409 (string detail) and other statuses", () => {
    expect(workflowChanged(err(409, "external_slug already in use"))).toBeNull();
    expect(workflowChanged(err(500, { code: "workflow_changed" }))).toBeNull();
    expect(workflowChanged(new Error("boom"))).toBeNull();
    expect(workflowChanged(null)).toBeNull();
  });
});
