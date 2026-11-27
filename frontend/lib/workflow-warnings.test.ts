import { describe, it, expect } from "vitest";
import { blocksRun, warningsByNode, type WorkflowWarning } from "./workflow-warnings";

const warning = (code: WorkflowWarning["code"], nodeId: string): WorkflowWarning => ({
  code,
  nodeId,
  branch: null,
  message: `${code} on ${nodeId}`,
});

describe("blocksRun", () => {
  it.each(["missing_branch", "join_mixed_inputs", "join_lane_escapes", "join_collision"] as const)(
    "treats %s as stopping the run",
    (code) => expect(blocksRun(warning(code, "n"))).toBe(true)
  );

  it("treats unknown_variable as advice only", () => {
    expect(blocksRun(warning("unknown_variable", "n"))).toBe(false);
  });
});

describe("warningsByNode", () => {
  it("groups messages under the node a join warning names", () => {
    const grouped = warningsByNode([
      warning("join_collision", "m"),
      warning("join_lane_escapes", "j"),
      warning("join_mixed_inputs", "j"),
    ]);
    expect(grouped).toEqual({
      m: ["join_collision on m"],
      j: ["join_lane_escapes on j", "join_mixed_inputs on j"],
    });
  });
});
