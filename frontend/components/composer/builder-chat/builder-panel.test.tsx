import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BuilderPanel } from "./builder-panel";

const { sendBuilderMessage } = vi.hoisted(() => ({ sendBuilderMessage: vi.fn() }));

vi.mock("@/lib/api/builder", () => ({ sendBuilderMessage }));

function reply(overrides: Record<string, unknown> = {}) {
  return {
    sessionId: "session-1",
    events: [],
    reply: "ok",
    workflow: null,
    validation_errors: [],
    ...overrides,
  };
}

describe("BuilderPanel session recovery", () => {
  beforeEach(() => {
    sendBuilderMessage.mockReset();
  });

  it("drops the stale session id after a session_expired turn", async () => {
    sendBuilderMessage
      .mockResolvedValueOnce(
        reply({ sessionId: "stale-session", events: [{ type: "session_expired" }] })
      )
      .mockResolvedValueOnce(reply({ sessionId: "session-2", reply: "fresh start" }));

    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);
    const input = screen.getByPlaceholderText("Describe your workflow…");

    fireEvent.change(input, { target: { value: "first" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("ok");

    fireEvent.change(input, { target: { value: "second" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("fresh start");

    expect(sendBuilderMessage).toHaveBeenCalledTimes(2);
    expect(sendBuilderMessage.mock.calls[0]?.[1]).toEqual({
      message: "first",
      sessionId: undefined,
    });
    // A stale id must not be echoed back: the next turn has to open a new session.
    expect(sendBuilderMessage.mock.calls[1]?.[1]).toEqual({
      message: "second",
      sessionId: undefined,
    });
  });
});
