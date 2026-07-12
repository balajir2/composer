import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import EndPanel from "./end";

describe("EndPanel", () => {
  it("does not render an outputRenderHint control — EndNodeData has no such field and EndExecutor never reads it", () => {
    const onChange = vi.fn();
    render(<EndPanel data={{}} onChange={onChange} currentNodeId="end-1" />);
    expect(screen.queryByText(/render hint/i)).not.toBeInTheDocument();
  });

  it("renders a note that End has no configurable fields", () => {
    const onChange = vi.fn();
    render(<EndPanel data={{}} onChange={onChange} currentNodeId="end-1" />);
    expect(screen.getByText(/no configurable/i)).toBeInTheDocument();
  });
});
