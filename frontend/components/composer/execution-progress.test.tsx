import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { subscribeExecution, type ComposerEventHandler } from "@/lib/ws";
import { ExecutionProgress } from "./execution-progress";

vi.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "token" } }) }));
vi.mock("@/lib/ws", () => ({ subscribeExecution: vi.fn() }));
afterEach(cleanup);
it("forwards each socket event and retains node progress, terminal output, and unsubscribe", () => {
  let receive: ComposerEventHandler = () => {};
  const unsubscribe = vi.fn();
  vi.mocked(subscribeExecution).mockImplementation((_id, _token, callback) => {
    receive = callback;
    return unsubscribe;
  });
  const onEvent = vi.fn();
  const onTerminal = vi.fn();
  const { unmount } = render(
    <ExecutionProgress
      executionId="run-1"
      initialStatus="running"
      onTerminal={onTerminal}
      onEvent={onEvent}
    />
  );
  const base = { executionId: "run-1", tenantId: null, timestamp: "2026-11-26T10:00:00Z" };
  act(() => receive({ ...base, type: "node_started", nodeId: "node-1", nodeName: "First step" }));
  expect(screen.getByText("First step")).toBeVisible();
  act(() => receive({ ...base, type: "node_completed", nodeId: "node-1", output: "node output" }));
  act(() =>
    receive({ ...base, type: "workflow_completed", status: "completed", output: "final output" })
  );
  expect(onEvent).toHaveBeenCalledTimes(3);
  expect(onTerminal).toHaveBeenCalledWith("completed", "final output");
  expect(subscribeExecution).toHaveBeenCalledTimes(1);
  unmount();
  expect(unsubscribe).toHaveBeenCalledTimes(1);
});
