import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("sends the execution id when asking about a failed run", async () => {
    sendBuilderMessage.mockResolvedValue(reply({}));

    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} executionId="exec-123" />);

    fireEvent.click(screen.getByRole("button", { name: /ask about the failed run/i }));

    await waitFor(() => {
      expect(sendBuilderMessage).toHaveBeenCalledWith("wf-1", {
        message: "Diagnose this failed run and suggest a fix.",
        sessionId: undefined,
        executionId: "exec-123",
      });
    });
  });
});

describe("BuilderPanel conversation UX", () => {
  beforeEach(() => {
    sendBuilderMessage.mockReset();
  });

  async function sendText(text: string) {
    const input = screen.getByPlaceholderText("Describe your workflow…");
    fireEvent.change(input, { target: { value: text } });
    fireEvent.keyDown(input, { key: "Enter" });
  }

  it("shows each message in its own bubble tagged by role", async () => {
    sendBuilderMessage.mockResolvedValue(reply({ reply: "Hello there" }));
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    await sendText("hi");
    await screen.findByText("Hello there");

    expect(screen.getByText("hi").closest("[data-role]")?.getAttribute("data-role")).toBe("user");
    expect(screen.getByText("Hello there").closest("[data-role]")?.getAttribute("data-role")).toBe(
      "assistant"
    );
  });

  it("offers example prompts on an empty conversation and sends the chosen one", async () => {
    sendBuilderMessage.mockResolvedValue(reply({}));
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    const chips = screen.getAllByTestId("builder-suggestion");
    expect(chips.length).toBeGreaterThan(0);
    fireEvent.click(chips[0]!);

    await waitFor(() => expect(sendBuilderMessage).toHaveBeenCalledTimes(1));
    expect(sendBuilderMessage.mock.calls[0]?.[1].message).toBe(chips[0]!.textContent);
    expect(screen.queryAllByTestId("builder-suggestion")).toHaveLength(0);
  });

  it("does not send on Shift+Enter, so replies can span lines", () => {
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);
    const input = screen.getByPlaceholderText("Describe your workflow…");

    fireEvent.change(input, { target: { value: "line one" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });

    expect(sendBuilderMessage).not.toHaveBeenCalled();
  });

  it("summarises what was added to the canvas", async () => {
    sendBuilderMessage.mockResolvedValue(
      reply({
        reply: "Built it.",
        events: [{ type: "stage_applied", node_count: 4, edge_count: 3 }],
      })
    );
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    await sendText("build");

    expect(await screen.findByText(/4 nodes/)).toBeTruthy();
    expect(screen.getByText(/3 connections/)).toBeTruthy();
  });

  it("shows validation notices instead of dropping them", async () => {
    sendBuilderMessage.mockResolvedValue(
      reply({ events: [{ type: "message", content: "Remaining issues: End node has no input" }] })
    );
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    await sendText("build");

    expect(await screen.findByText(/Remaining issues/)).toBeTruthy();
  });

  it("approves a plan once, then locks the button", async () => {
    sendBuilderMessage
      .mockResolvedValueOnce(
        reply({ events: [{ type: "plan", stages: [{ id: "s1", summary: "Search the web" }] }] })
      )
      .mockResolvedValueOnce(reply({ reply: "Done" }));
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    await sendText("search");
    const approve = await screen.findByRole("button", { name: /approve plan/i });
    fireEvent.click(approve);
    await screen.findByText("Done");

    expect(sendBuilderMessage).toHaveBeenCalledTimes(2);
    expect((screen.getByRole("button", { name: /approved/i }) as HTMLButtonElement).disabled).toBe(
      true
    );
  });

  it("reports a failed request in the conversation and recovers", async () => {
    sendBuilderMessage.mockRejectedValueOnce(new Error("boom"));
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    await sendText("build");

    expect(await screen.findByText(/something went wrong/i)).toBeTruthy();
    expect(
      (screen.getByPlaceholderText("Describe your workflow…") as HTMLTextAreaElement).disabled
    ).toBe(false);
  });
});
