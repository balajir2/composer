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
});
