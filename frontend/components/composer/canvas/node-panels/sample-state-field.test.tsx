import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, renderHook, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SampleStateField, useSampleState } from "./sample-state-field";

const { listExecutions } = vi.hoisted(() => ({ listExecutions: vi.fn() }));
vi.mock("@/lib/api/executions", () => ({ listExecutions }));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

// renderHook's `wrapper` option is invoked as a component, i.e. with a
// `{ children }` props object rather than a bare ReactNode — adapt `wrap`
// (which is called directly elsewhere as `wrap(<Node />)`) to that calling
// convention here instead of changing its signature.
function wrapperComponent({ children }: { children: React.ReactNode }) {
  return wrap(children);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useSampleState", () => {
  it("pre-fills from the workflow's most recent execution", async () => {
    listExecutions.mockResolvedValue({
      total: 1,
      items: [{ id: "e1", variables: { counter: 3 }, nodeResults: {} }],
      limit: 1,
      offset: 0,
    });
    const { result } = renderHook(() => useSampleState("wf-1"), { wrapper: wrapperComponent });
    await waitFor(() => expect(result.current.text).toContain("counter"));
    expect(result.current.parsed).toEqual({ counter: 3 });
    expect(result.current.error).toBeNull();
  });

  it("starts as an empty object when the workflow has no executions", async () => {
    listExecutions.mockResolvedValue({ total: 0, items: [], limit: 1, offset: 0 });
    const { result } = renderHook(() => useSampleState("wf-1"), { wrapper: wrapperComponent });
    await waitFor(() => expect(listExecutions).toHaveBeenCalled());
    expect(result.current.parsed).toEqual({});
  });

  it("does not fetch when workflowId is undefined", () => {
    renderHook(() => useSampleState(undefined), { wrapper: wrapperComponent });
    expect(listExecutions).not.toHaveBeenCalled();
  });

  it("does not clobber a user's edit that happens before the pre-fill fetch resolves", async () => {
    let resolveFetch: (value: unknown) => void = () => {};
    listExecutions.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      })
    );
    const { result } = renderHook(() => useSampleState("wf-1"), { wrapper: wrapperComponent });

    act(() => {
      result.current.setText('{"userTyped": true}');
    });

    await act(async () => {
      resolveFetch({
        total: 1,
        items: [{ id: "e1", variables: { counter: 3 }, nodeResults: {} }],
        limit: 1,
        offset: 0,
      });
    });

    await waitFor(() => expect(listExecutions).toHaveBeenCalled());
    expect(result.current.text).toBe('{"userTyped": true}');
  });
});

describe("SampleStateField", () => {
  it("shows an error message when the text is invalid JSON", () => {
    render(
      wrap(<SampleStateField text="{not json" onChangeText={vi.fn()} error="Invalid JSON" />)
    );
    expect(screen.getByText("Invalid JSON")).toBeInTheDocument();
  });

  it("calls onChangeText as the textarea is edited", () => {
    const onChangeText = vi.fn();
    render(wrap(<SampleStateField text="{}" onChangeText={onChangeText} error={null} />));
    fireEvent.change(screen.getByLabelText(/Sample state/i), {
      target: { value: '{"counter": 1}' },
    });
    expect(onChangeText).toHaveBeenCalledWith('{"counter": 1}');
  });
});
