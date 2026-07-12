import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DataTransformPanel from "./data-transform";

describe("DataTransformPanel", () => {
  it("renders operation choices and calls onChange with operation", () => {
    const onChange = vi.fn();
    render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);
    fireEvent.change(screen.getByLabelText("Operation"), { target: { value: "filter" } });
    expect(onChange).toHaveBeenCalledWith({ operation: "filter" });
  });

  it("editing the collection field calls onChange with collection", () => {
    const onChange = vi.fn();
    render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);
    fireEvent.change(screen.getByLabelText(/Collection/), {
      target: { value: "lastOutput.items" },
    });
    expect(onChange).toHaveBeenCalledWith({ collection: "lastOutput.items" });
  });

  it("editing the expression field calls onChange with expression", () => {
    const onChange = vi.fn();
    render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);
    fireEvent.change(screen.getByLabelText("Per-item expression"), {
      target: { value: "item.active" },
    });
    expect(onChange).toHaveBeenCalledWith({ expression: "item.active" });
  });

  it("editing the item variable name calls onChange with itemVar", () => {
    const onChange = vi.fn();
    render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);
    fireEvent.change(screen.getByLabelText(/Item variable/), {
      target: { value: "row" },
    });
    expect(onChange).toHaveBeenCalledWith({ itemVar: "row" });
  });

  it("shows the initial-value field only when operation is reduce, and calls onChange with initial", () => {
    const onChange = vi.fn();
    render(
      <DataTransformPanel data={{ operation: "reduce" }} onChange={onChange} currentNodeId="dt-1" />
    );
    fireEvent.change(screen.getByLabelText(/Initial value/), {
      target: { value: "0" },
    });
    expect(onChange).toHaveBeenCalledWith({ initial: 0 });
  });

  it("describes the expression language as simpleeval, not JSONPath/Handlebars", () => {
    const onChange = vi.fn();
    render(<DataTransformPanel data={{}} onChange={onChange} currentNodeId="dt-1" />);
    expect(screen.queryByText(/JSONPath/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Handlebars/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/simpleeval/).length).toBeGreaterThan(0);
  });
});
