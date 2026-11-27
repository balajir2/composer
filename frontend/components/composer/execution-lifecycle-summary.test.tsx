import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ExecutionObservabilityRead } from "@/lib/api/executions";
import { ExecutionLifecycleSummary } from "./execution-lifecycle-summary";

const base: ExecutionObservabilityRead = {
  executionId: "exec-1",
  workflowId: "flow-1",
  status: "running",
  currentNodeId: "node-a",
  startedAt: "2026-11-26T10:00:00Z",
  completedAt: null,
  historyAvailable: true,
  attemptCount: 1,
  failureGuidance: null,
  recoveryState: "not_applicable",
  observedAt: "2026-11-26T10:01:00Z",
};

function renderSummary(overrides: Partial<ExecutionObservabilityRead> = {}, stale = false) {
  return render(<ExecutionLifecycleSummary summary={{ ...base, ...overrides }} stale={stale} />);
}

describe("ExecutionLifecycleSummary", () => {
  it.each([
    ["running", "In progress"],
    ["waiting_approval", "Waiting for approval"],
    ["completed", "Completed"],
    ["cancelled", "Canceled"],
    ["failed", "Failed"],
  ])("labels %s status safely", (status, label) => {
    renderSummary({ status });
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("explains canceled executions are ineligible for recovery", () => {
    renderSummary({ status: "cancelled", recoveryState: "ineligible_canceled" });
    expect(screen.getByText(/cannot be recovered/i)).toBeInTheDocument();
  });

  it("explains failures require future assessment and shows safe guidance", () => {
    renderSummary({
      status: "failed",
      recoveryState: "assessment_required",
      failureGuidance: "Review the workflow configuration.",
    });
    expect(screen.getByText(/safe recovery assessment/i)).toBeInTheDocument();
    expect(screen.getByText("Review the workflow configuration.")).toBeInTheDocument();
  });

  it("marks stale summaries and uses a generic label for unknown statuses", () => {
    renderSummary({ status: "future_state" }, true);
    expect(screen.getByText(/status unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/may be out of date/i)).toBeInTheDocument();
  });

  it("does not offer recovery controls", () => {
    renderSummary({ status: "failed", recoveryState: "assessment_required" });
    expect(screen.queryByRole("button")).toBeNull();
  });
});
