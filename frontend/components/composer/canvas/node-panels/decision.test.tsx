import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DecisionPanel from "./decision";

vi.mock("@/lib/api/llm-models", () => ({
  listEnabledLlmModels: vi.fn().mockResolvedValue([]),
}));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function baseData(overrides: Record<string, unknown> = {}) {
  return { mode: "binary", instruction: "Is this urgent?", ...overrides };
}

describe("DecisionPanel", () => {
  it("defaults provider to llm when unset", () => {
    render(wrap(<DecisionPanel data={baseData()} onChange={vi.fn()} />));
    expect(screen.getByDisplayValue("LLM")).toBeInTheDocument();
  });

  it("switching provider to TypeSafe calls onChange with provider: typesafe", () => {
    const onChange = vi.fn();
    render(wrap(<DecisionPanel data={baseData()} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "typesafe" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ provider: "typesafe" }));
  });

  it("shows the zero-shot hint when examples is empty", () => {
    render(wrap(<DecisionPanel data={baseData()} onChange={vi.fn()} />));
    expect(screen.getByText(/Zero-shot decisions can be inconsistent/)).toBeInTheDocument();
  });

  it("hides the zero-shot hint once an example is added", () => {
    render(
      wrap(
        <DecisionPanel
          data={baseData({ examples: [{ input: "a", result: true }] })}
          onChange={vi.fn()}
        />
      )
    );
    expect(screen.queryByText(/Zero-shot decisions can be inconsistent/)).not.toBeInTheDocument();
  });

  it("adding an example calls onChange with the new examples array", () => {
    const onChange = vi.fn();
    render(wrap(<DecisionPanel data={baseData()} onChange={onChange} />));
    fireEvent.click(screen.getByRole("button", { name: /add example/i }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ examples: [{ input: "", result: false }] })
    );
  });

  it("choice mode shows the options editor, not true/false labels", () => {
    render(
      wrap(
        <DecisionPanel
          data={baseData({ mode: "choice", options: [{ label: "billing" }, { label: "technical" }] })}
          onChange={vi.fn()}
        />
      )
    );
    expect(screen.getByDisplayValue("billing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add option/i })).toBeInTheDocument();
  });

  it("shows the TypeSafe examples-are-folded note only when provider is typesafe", () => {
    render(wrap(<DecisionPanel data={baseData({ provider: "typesafe" })} onChange={vi.fn()} />));
    expect(screen.getByText(/folded into criteria text/i)).toBeInTheDocument();
  });
});
