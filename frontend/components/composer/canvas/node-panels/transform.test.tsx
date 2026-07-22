import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TransformPanel from "./transform";

const { listExecutions } = vi.hoisted(() => ({ listExecutions: vi.fn() }));
vi.mock("@/lib/api/executions", () => ({ listExecutions }));

const { evaluateTransformExpression } = vi.hoisted(() => ({
  evaluateTransformExpression: vi.fn(),
}));
vi.mock("@/lib/api/expressions", () => ({ evaluateTransformExpression }));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  listExecutions.mockResolvedValue({ total: 0, items: [], limit: 1, offset: 0 });
});

describe("TransformPanel", () => {
  it("editing the expression calls onChange with transformScript", () => {
    const onChange = vi.fn();
    render(wrap(<TransformPanel data={{}} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("Expression"), {
      target: { value: "lastOutput.upper()" },
    });
    expect(onChange).toHaveBeenCalledWith({ transformScript: "lastOutput.upper()" });
  });

  it("Test button is disabled when the expression is empty", () => {
    render(wrap(<TransformPanel data={{}} onChange={vi.fn()} />));
    expect(screen.getByRole("button", { name: "Test" })).toBeDisabled();
  });

  it("runs the test and shows the result on success", async () => {
    evaluateTransformExpression.mockResolvedValue({ ok: true, result: "HI", error: null });
    render(
      wrap(
        <TransformPanel
          data={{ transformScript: "lastOutput.upper()" }}
          onChange={vi.fn()}
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText(/"HI"|HI/)).toBeInTheDocument());
    expect(evaluateTransformExpression).toHaveBeenCalledWith({
      expression: "lastOutput.upper()",
      variables: {},
    });
  });

  it("shows the error message on a failed test", async () => {
    evaluateTransformExpression.mockResolvedValue({
      ok: false,
      result: null,
      error: "simpleeval failed evaluating 'bad': NameNotDefined: bad is not defined",
    });
    render(
      wrap(<TransformPanel data={{ transformScript: "bad" }} onChange={vi.fn()} workflowId="wf-1" />)
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() =>
      expect(screen.getByText(/NameNotDefined/)).toBeInTheDocument()
    );
  });

  it("Test button is disabled when the expression is only whitespace", () => {
    render(wrap(<TransformPanel data={{ transformScript: "   " }} onChange={vi.fn()} />));
    expect(screen.getByRole("button", { name: "Test" })).toBeDisabled();
  });

  it("clears a stale result once the expression is edited", async () => {
    evaluateTransformExpression.mockResolvedValue({ ok: true, result: "HI", error: null });
    const { rerender } = render(
      wrap(
        <TransformPanel
          data={{ transformScript: "lastOutput.upper()" }}
          onChange={vi.fn()}
          workflowId="wf-1"
        />
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await waitFor(() => expect(screen.getByText("HI")).toBeInTheDocument());

    rerender(
      wrap(
        <TransformPanel
          data={{ transformScript: "lastOutput.lower()" }}
          onChange={vi.fn()}
          workflowId="wf-1"
        />
      )
    );

    await waitFor(() => expect(screen.queryByText("HI")).not.toBeInTheDocument());
  });
});
