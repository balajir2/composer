import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BuilderPanel } from "./builder-panel";

const { sendBuilderMessage, listBuilderModels } = vi.hoisted(() => ({
  sendBuilderMessage: vi.fn(),
  listBuilderModels: vi.fn(),
}));

vi.mock("@/lib/api/builder", () => ({ sendBuilderMessage, listBuilderModels }));

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
    listBuilderModels.mockReset();
    listBuilderModels.mockResolvedValue({
      default: "anthropic/sonnet",
      models: [{ id: "anthropic/sonnet", label: "Sonnet" }],
    });
    window.localStorage.clear();
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
      model: "anthropic/sonnet",
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
    listBuilderModels.mockReset();
    listBuilderModels.mockResolvedValue({
      default: "anthropic/sonnet",
      models: [{ id: "anthropic/sonnet", label: "Sonnet" }],
    });
    window.localStorage.clear();
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

describe("BuilderPanel model choice", () => {
  beforeEach(() => {
    sendBuilderMessage.mockReset();
    listBuilderModels.mockReset();
    window.localStorage.clear();
  });

  const twoModels = {
    default: "anthropic/sonnet",
    models: [
      { id: "anthropic/sonnet", label: "Sonnet" },
      { id: "openai/gpt-5", label: "GPT-5" },
    ],
  };

  it("lets the user pick from the admin-allowed models and sends the choice", async () => {
    listBuilderModels.mockResolvedValue(twoModels);
    sendBuilderMessage.mockResolvedValue(reply({}));
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    const picker = (await screen.findByLabelText(/model/i)) as HTMLSelectElement;
    expect(picker.value).toBe("anthropic/sonnet");
    fireEvent.change(picker, { target: { value: "openai/gpt-5" } });

    const input = screen.getByPlaceholderText("Describe your workflow…");
    fireEvent.change(input, { target: { value: "build" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(sendBuilderMessage).toHaveBeenCalledTimes(1));
    expect(sendBuilderMessage.mock.calls[0]?.[1].model).toBe("openai/gpt-5");
  });

  it("shows the model as plain text when the admin allowed only one", async () => {
    listBuilderModels.mockResolvedValue({
      default: "anthropic/sonnet",
      models: [{ id: "anthropic/sonnet", label: "Sonnet" }],
    });
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    expect(await screen.findByText("Sonnet")).toBeTruthy();
    expect(screen.queryByLabelText(/model/i)).toBeNull();
  });

  it("ignores a remembered model the admin has since removed", async () => {
    window.localStorage.setItem("composer.builder.model", "groq/removed");
    listBuilderModels.mockResolvedValue(twoModels);
    sendBuilderMessage.mockResolvedValue(reply({}));
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    const picker = (await screen.findByLabelText(/model/i)) as HTMLSelectElement;

    expect(picker.value).toBe("anthropic/sonnet");
  });

  it("still works when the model list cannot be loaded", async () => {
    listBuilderModels.mockRejectedValue(new Error("down"));
    sendBuilderMessage.mockResolvedValue(reply({ reply: "fine" }));
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} />);

    const input = screen.getByPlaceholderText("Describe your workflow…");
    fireEvent.change(input, { target: { value: "build" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await screen.findByText("fine");
    expect(sendBuilderMessage.mock.calls[0]?.[1].model).toBeUndefined();
  });
});

// 2026-11-19 defect: the builder reads the workflow from the database, but
// canvas edits autosave on a 3 s debounce. Deleting edges and then messaging
// the builder inside that window made it work from (and write back) the stale
// graph, so the deleted edges reappeared. Unsaved edits must land first.
describe("BuilderPanel flushes unsaved canvas edits", () => {
  beforeEach(() => {
    sendBuilderMessage.mockReset();
    listBuilderModels.mockReset();
    listBuilderModels.mockResolvedValue({ default: "a/m", models: [{ id: "a/m", label: "M" }] });
  });

  function type(text: string) {
    const input = screen.getByPlaceholderText("Describe your workflow…");
    fireEvent.change(input, { target: { value: text } });
    fireEvent.keyDown(input, { key: "Enter" });
  }

  it("awaits the flush before the builder request goes out", async () => {
    const order: string[] = [];
    const beforeSend = vi.fn(async () => {
      order.push("flush");
    });
    sendBuilderMessage.mockImplementation(async () => {
      order.push("send");
      return reply();
    });
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} beforeSend={beforeSend} />);

    type("remove the duplicate edges");
    await screen.findByText("ok");

    expect(order).toEqual(["flush", "send"]);
  });

  it("does not run the builder on a stale graph when the save fails", async () => {
    const beforeSend = vi.fn().mockRejectedValue(new Error("save failed"));
    render(<BuilderPanel workflowId="wf-1" onWorkflowUpdated={vi.fn()} beforeSend={beforeSend} />);

    type("tidy up");

    expect(await screen.findByText(/couldn't save your latest canvas edits/i)).toBeTruthy();
    expect(sendBuilderMessage).not.toHaveBeenCalled();
  });
});

describe("BuilderPanel optimistic locking", () => {
  const SEEN = "2026-11-24T10:00:00.123000Z";
  const changed = {
    status: 409,
    detail: {
      detail: {
        code: "workflow_changed",
        message: "workflow changed since you loaded it",
        currentUpdatedAt: "2026-11-24T10:05:00.500000Z",
      },
    },
  };

  beforeEach(() => {
    sendBuilderMessage.mockReset();
    listBuilderModels.mockReset();
    listBuilderModels.mockResolvedValue({
      default: "anthropic/sonnet",
      models: [{ id: "anthropic/sonnet", label: "Sonnet" }],
    });
    window.localStorage.clear();
  });

  function send(text: string) {
    const input = screen.getByPlaceholderText("Describe your workflow…");
    fireEvent.change(input, { target: { value: text } });
    fireEvent.keyDown(input, { key: "Enter" });
  }

  it("sends the last-seen updatedAt with the message", async () => {
    sendBuilderMessage.mockResolvedValue(reply());
    render(
      <BuilderPanel
        workflowId="wf-1"
        onWorkflowUpdated={vi.fn()}
        getExpectedUpdatedAt={() => SEEN}
      />
    );
    send("hello");
    await screen.findByText("ok");
    expect(sendBuilderMessage.mock.calls[0]?.[1]).toMatchObject({
      message: "hello",
      expectedUpdatedAt: SEEN,
    });
  });

  it("opens the dialog on a 409, and Overwrite resends without the precondition", async () => {
    sendBuilderMessage
      .mockRejectedValueOnce(changed)
      .mockResolvedValueOnce(reply({ reply: "done anyway" }));
    render(
      <BuilderPanel
        workflowId="wf-1"
        onWorkflowUpdated={vi.fn()}
        getExpectedUpdatedAt={() => SEEN}
      />
    );
    send("build it");
    await screen.findByText("This workflow changed");

    fireEvent.click(screen.getByRole("button", { name: "Overwrite" }));

    await screen.findByText("done anyway");
    expect(sendBuilderMessage).toHaveBeenCalledTimes(2);
    expect(sendBuilderMessage.mock.calls[1]?.[1].message).toBe("build it");
    expect(sendBuilderMessage.mock.calls[1]?.[1].expectedUpdatedAt).toBeUndefined();
  });

  it("Reload asks the page to refetch and does not resend", async () => {
    sendBuilderMessage.mockRejectedValueOnce(changed);
    const onReload = vi.fn().mockResolvedValue(undefined);
    render(
      <BuilderPanel
        workflowId="wf-1"
        onWorkflowUpdated={vi.fn()}
        getExpectedUpdatedAt={() => SEEN}
        onReload={onReload}
      />
    );
    send("build it");
    await screen.findByText("This workflow changed");

    fireEvent.click(screen.getByRole("button", { name: "Reload" }));

    await waitFor(() => expect(onReload).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText("This workflow changed")).toBeNull());
    expect(sendBuilderMessage).toHaveBeenCalledTimes(1);
  });
});
