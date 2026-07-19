import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import JiraPanel from "./jira";

const { listEnabledLlmModels } = vi.hoisted(() => ({
  listEnabledLlmModels: vi.fn(),
}));

vi.mock("@/lib/api/llm-models", () => ({ listEnabledLlmModels }));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  listEnabledLlmModels.mockResolvedValue([]);
});

describe("JiraPanel — operation toggle", () => {
  it("defaults to agent mode and shows the Prompt field", () => {
    render(wrap(<JiraPanel data={{}} onChange={vi.fn()} />));
    expect(screen.getByText("Prompt")).toBeInTheDocument();
    expect(screen.queryByLabelText("JQL")).not.toBeInTheDocument();
  });

  it("switching to extract mode calls onChange and reveals extract fields", () => {
    const onChange = vi.fn();
    const { rerender } = render(wrap(<JiraPanel data={{}} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("Operation"), {
      target: { value: "extract" },
    });
    expect(onChange).toHaveBeenCalledWith({ operation: "extract" });

    rerender(wrap(<JiraPanel data={{ operation: "extract" }} onChange={onChange} />));
    expect(screen.getByLabelText("JQL")).toBeInTheDocument();
    expect(screen.queryByText("Prompt")).not.toBeInTheDocument();
  });

  it("editing JQL calls onChange with jql", () => {
    const onChange = vi.fn();
    render(wrap(<JiraPanel data={{ operation: "extract" }} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("JQL"), {
      target: { value: "project = MB" },
    });
    expect(onChange).toHaveBeenCalledWith({ jql: "project = MB" });
  });

  it("editing the comma-separated fields list calls onChange with an array", () => {
    const onChange = vi.fn();
    render(wrap(<JiraPanel data={{ operation: "extract" }} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("Fields"), {
      target: { value: "summary, status, priority" },
    });
    expect(onChange).toHaveBeenCalledWith({ fields: ["summary", "status", "priority"] });
  });

  it("toggling expand changelog calls onChange with expandChangelog", () => {
    const onChange = vi.fn();
    render(
      wrap(
        <JiraPanel
          data={{ operation: "extract", expandChangelog: true }}
          onChange={onChange}
        />
      )
    );
    fireEvent.click(screen.getByLabelText("Expand changelog"));
    expect(onChange).toHaveBeenCalledWith({ expandChangelog: false });
  });

  it("editing max issues calls onChange with a number", () => {
    const onChange = vi.fn();
    render(wrap(<JiraPanel data={{ operation: "extract" }} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("Max issues"), {
      target: { value: "2000" },
    });
    expect(onChange).toHaveBeenCalledWith({ maxIssues: 2000 });
  });

  it("clamps max issues below 1 up to 1, and above 5000 down to 5000", () => {
    const onChange = vi.fn();
    render(wrap(<JiraPanel data={{ operation: "extract" }} onChange={onChange} />));
    const input = screen.getByLabelText("Max issues");

    fireEvent.change(input, { target: { value: "0" } });
    expect(onChange).toHaveBeenLastCalledWith({ maxIssues: 1 });

    fireEvent.change(input, { target: { value: "9999" } });
    expect(onChange).toHaveBeenLastCalledWith({ maxIssues: 5000 });
  });

  it("clearing max issues to an empty string emits undefined (not coerced to 1)", () => {
    const onChange = vi.fn();
    render(
      wrap(<JiraPanel data={{ operation: "extract", maxIssues: 2000 }} onChange={onChange} />)
    );
    fireEvent.change(screen.getByLabelText("Max issues"), {
      target: { value: "" },
    });
    expect(onChange).toHaveBeenCalledWith({ maxIssues: undefined });
  });

  it("typing a comma-separated field list character-by-character does not corrupt entries", () => {
    // Simulates a real controlled-input round trip: the parent (this test)
    // applies each onChange patch back onto `data` and re-renders, exactly
    // like the real NodePanel wrapper does. Each keystroke reads the
    // *currently displayed* input value (which would reflect any bogus
    // snap-back from a re-derived-from-parsed-array bug) and appends the
    // next intended character — not a single change with the whole final
    // string, which would never exercise the trailing-comma drop.
    let data: Record<string, unknown> = { operation: "extract" };
    const onChange = vi.fn((patch: Record<string, unknown>) => {
      data = { ...data, ...patch };
    });

    const { rerender } = render(wrap(<JiraPanel data={data} onChange={onChange} />));
    const rerenderWithLatestData = () =>
      rerender(wrap(<JiraPanel data={data} onChange={onChange} />));

    const keystrokes = "summary,status".split("");
    for (const ch of keystrokes) {
      const input = screen.getByLabelText("Fields") as HTMLInputElement;
      const next = input.value + ch;
      fireEvent.change(input, { target: { value: next } });
      rerenderWithLatestData();
    }

    expect(onChange).toHaveBeenLastCalledWith({ fields: ["summary", "status"] });
  });

  it("resyncs the Fields text when switching to a different node, not just on operation change", () => {
    // JiraPanel stays mounted across node selection (no key={node.id} in
    // property-panel.tsx), so switching from one extract-mode Jira node to
    // another with the same operation must still re-seed the Fields text
    // from the newly-selected node's data — otherwise the previous node's
    // stale text lingers and corrupts the new node's fields on the next edit.
    const onChange = vi.fn();
    const { rerender } = render(
      wrap(
        <JiraPanel
          data={{ operation: "extract", fields: ["summary", "status"] }}
          onChange={onChange}
          currentNodeId="node-a"
        />
      )
    );
    expect((screen.getByLabelText("Fields") as HTMLInputElement).value).toBe(
      "summary, status"
    );

    // Simulate the Designer selecting a different Jira node — same
    // operation, different underlying data, panel stays mounted in place.
    rerender(
      wrap(
        <JiraPanel
          data={{ operation: "extract", fields: ["priority"] }}
          onChange={onChange}
          currentNodeId="node-b"
        />
      )
    );

    expect((screen.getByLabelText("Fields") as HTMLInputElement).value).toBe(
      "priority"
    );
  });
});
