import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import DataTransformPanel from "./data-transform";

const { listExecutions } = vi.hoisted(() => ({ listExecutions: vi.fn() }));
vi.mock("@/lib/api/executions", () => ({ listExecutions }));

const { evaluateDataTransformExpression } = vi.hoisted(() => ({
  evaluateDataTransformExpression: vi.fn(),
}));
vi.mock("@/lib/api/expressions", () => ({ evaluateDataTransformExpression }));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  listExecutions.mockResolvedValue({ total: 0, items: [], limit: 1, offset: 0 });
});

describe("DataTransformPanel", () => {
  it("renders operation choices and calls onChange with operation", () => {
    const onChange = vi.fn();
    render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));
    fireEvent.change(screen.getByLabelText("Operation"), { target: { value: "filter" } });
    expect(onChange).toHaveBeenCalledWith({ operation: "filter" });
  });

  it("editing the collection field calls onChange with collection", () => {
    const onChange = vi.fn();
    render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));
    fireEvent.change(screen.getByLabelText(/Collection/), {
      target: { value: "lastOutput.items" },
    });
    expect(onChange).toHaveBeenCalledWith({ collection: "lastOutput.items" });
  });

  it("editing the expression field calls onChange with expression", () => {
    const onChange = vi.fn();
    render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));
    fireEvent.change(screen.getByLabelText("Per-item expression"), {
      target: { value: "item.active" },
    });
    expect(onChange).toHaveBeenCalledWith({ expression: "item.active" });
  });

  it("editing the item variable name calls onChange with itemVar", () => {
    const onChange = vi.fn();
    render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));
    fireEvent.change(screen.getByLabelText(/Item variable/), {
      target: { value: "row" },
    });
    expect(onChange).toHaveBeenCalledWith({ itemVar: "row" });
  });

  it("shows the initial-value field only when operation is reduce, and calls onChange with initial", () => {
    const onChange = vi.fn();
    render(
      wrap(<DataTransformPanel data={{ operation: "reduce" }} onChange={onChange} currentNodeId="dt-1" />)
    );
    fireEvent.change(screen.getByLabelText(/Initial value/), {
      target: { value: "0" },
    });
    expect(onChange).toHaveBeenCalledWith({ initial: 0 });
  });

  it("describes the expression language as simpleeval, not JSONPath/Handlebars", () => {
    const onChange = vi.fn();
    render(wrap(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />));
    expect(screen.queryByText(/JSONPath/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Handlebars/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/simpleeval/).length).toBeGreaterThan(0);
  });

  it("Test button is disabled when the collection or expression is empty", () => {
    render(wrap(<DataTransformPanel data={{}} onChange={vi.fn()} currentNodeId="dt-1" />));
    expect(screen.getByRole("button", { name: "Test" })).toBeDisabled();
  });

  it("runs a map test and shows the result array", async () => {
    evaluateDataTransformExpression.mockResolvedValue({
      ok: true,
      result: [2, 4, 6],
      error: null,
      itemCount: 3,
      truncated: false,
    });
    render(
      wrap(
        <DataTransformPanel
          data={{ operation: "map", collection: "variables['nums']", expression: "item * 2" }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText(/\[.*2.*4.*6.*\]|2,\s*4,\s*6/s)).toBeInTheDocument());
    expect(evaluateDataTransformExpression).toHaveBeenCalledWith({
      operation: "map",
      collection: "variables['nums']",
      expression: "item * 2",
      itemVar: "item",
      initial: undefined,
      variables: {},
    });
  });

  it("shows a string reduce result unquoted, not JSON-escaped", async () => {
    evaluateDataTransformExpression.mockResolvedValue({
      ok: true,
      result: "assembled text",
      error: null,
      itemCount: 2,
      truncated: false,
    });
    render(
      wrap(
        <DataTransformPanel
          data={{
            operation: "reduce",
            collection: "variables['nums']",
            expression: "acc + item",
            initial: "",
          }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText("assembled text")).toBeInTheDocument());
    expect(screen.queryByText('"assembled text"')).not.toBeInTheDocument();
  });

  it("shows the truncation banner when the backend reports truncated", async () => {
    evaluateDataTransformExpression.mockResolvedValue({
      ok: true,
      result: [1],
      error: null,
      itemCount: 50,
      truncated: true,
    });
    render(
      wrap(
        <DataTransformPanel
          data={{ operation: "map", collection: "variables['nums']", expression: "item" }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText(/first 50/i)).toBeInTheDocument());
  });

  it("shows the error message on a failed test", async () => {
    evaluateDataTransformExpression.mockResolvedValue({
      ok: false,
      result: null,
      error: "collection: simpleeval failed evaluating \"variables['missing']\": KeyError",
      itemCount: null,
      truncated: false,
    });
    render(
      wrap(
        <DataTransformPanel
          data={{ operation: "map", collection: "variables['missing']", expression: "item" }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText(/KeyError/)).toBeInTheDocument());
  });

  it("Test button is disabled when the collection/expression are only whitespace", () => {
    render(
      wrap(
        <DataTransformPanel
          data={{ collection: "   ", expression: "   " }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
        />
      )
    );
    expect(screen.getByRole("button", { name: "Test" })).toBeDisabled();
  });

  it("clears a stale result once the collection or expression is edited", async () => {
    evaluateDataTransformExpression.mockResolvedValue({
      ok: true,
      result: [2, 4, 6],
      error: null,
      itemCount: 3,
      truncated: false,
    });
    const { rerender } = render(
      wrap(
        <DataTransformPanel
          data={{ operation: "map", collection: "variables['nums']", expression: "item * 2" }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText(/2,\s*4,\s*6/s)).toBeInTheDocument());

    rerender(
      wrap(
        <DataTransformPanel
          data={{ operation: "map", collection: "variables['nums']", expression: "item * 3" }}
          onChange={vi.fn()}
          currentNodeId="dt-1"
          workflowId="wf-1"
        />
      )
    );

    await waitFor(() => expect(screen.queryByText(/2,\s*4,\s*6/s)).not.toBeInTheDocument());
  });
});
