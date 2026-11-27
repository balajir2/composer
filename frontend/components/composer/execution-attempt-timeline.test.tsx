import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ExecutionAttemptPage } from "@/lib/api/executions";
import { ExecutionAttemptTimeline } from "./execution-attempt-timeline";

const attempt = (overrides: Partial<ExecutionAttemptPage["items"][number]> = {}) => ({
  id: "attempt-1",
  ordinal: 1,
  startedAt: "2026-11-26T10:00:00Z",
  endedAt: "2026-11-26T10:01:00Z",
  result: "failed",
  triggerKind: "initial",
  failureGuidance: "Check the workflow configuration.",
  hasApproval: false,
  ...overrides,
});
const page = (
  items: ExecutionAttemptPage["items"],
  historyAvailable = true
): ExecutionAttemptPage => ({ items, total: items.length, limit: 50, offset: 0, historyAvailable });

describe("ExecutionAttemptTimeline", () => {
  it("shows a loading state", () => {
    render(
      <ExecutionAttemptTimeline page={undefined} isLoading isError={false} onRetry={vi.fn()} />
    );
    expect(screen.getByRole("status")).toHaveTextContent(/loading attempt history/i);
  });

  it("shows a refresh error and retry control", () => {
    const onRetry = vi.fn();
    render(
      <ExecutionAttemptTimeline
        page={page([attempt()])}
        isLoading={false}
        isError
        onRetry={onRetry}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.getByText(/could not refresh attempt history/i)).toBeInTheDocument();
  });

  it("distinguishes legacy history from empty durable history", () => {
    const { rerender } = render(
      <ExecutionAttemptTimeline
        page={page([], false)}
        isLoading={false}
        isError={false}
        onRetry={vi.fn()}
      />
    );
    expect(screen.getByText(/before attempt history was recorded/i)).toBeInTheDocument();
    rerender(
      <ExecutionAttemptTimeline
        page={page([])}
        isLoading={false}
        isError={false}
        onRetry={vi.fn()}
      />
    );
    expect(screen.getByText(/no attempts to display/i)).toBeInTheDocument();
  });

  it("orders attempts by ordinal and associates approvals", () => {
    render(
      <ExecutionAttemptTimeline
        page={page([
          attempt({
            id: "2",
            ordinal: 2,
            result: "completed",
            triggerKind: "approval_resume",
            hasApproval: true,
          }),
          attempt({ id: "1", ordinal: 1 }),
        ])}
        isLoading={false}
        isError={false}
        onRetry={vi.fn()}
      />
    );
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Attempt 1");
    expect(items[1]).toHaveTextContent("Attempt 2");
    expect(items[1]).toHaveTextContent(/approval associated/i);
    expect(items[1]).toHaveTextContent(/resumed after approval/i);
  });

  it("uses safe generic result and trigger labels", () => {
    render(
      <ExecutionAttemptTimeline
        page={page([
          attempt({
            result: "secret_result",
            triggerKind: "secret_trigger",
            failureGuidance: null,
          }),
        ])}
        isLoading={false}
        isError={false}
        onRetry={vi.fn()}
      />
    );
    expect(screen.getByText(/result unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/trigger unavailable/i)).toBeInTheDocument();
  });
});
