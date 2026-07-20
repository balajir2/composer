import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import StartPanel from "./start";

function dataWithField(overrides: Record<string, unknown> = {}) {
  return {
    inputVariables: [
      { name: "report_date", type: "date", required: false, description: "", ...overrides },
    ],
  };
}

describe("StartPanel — date/datetime field types", () => {
  it("offers Date and Date & Time in the type dropdown", () => {
    render(<StartPanel data={dataWithField()} onChange={vi.fn()} />);
    const select = screen.getByDisplayValue("date") as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toContain("date");
    expect(optionValues).toContain("datetime");
  });

  it("renders a date picker trigger instead of a plain input for a date-typed field", () => {
    render(<StartPanel data={dataWithField()} onChange={vi.fn()} />);
    expect(screen.getByText("Select a date")).toBeInTheDocument();
  });

  it("renders a date+time picker trigger for a datetime-typed field", () => {
    render(<StartPanel data={dataWithField({ type: "datetime" })} onChange={vi.fn()} />);
    expect(screen.getByText("Select a date & time")).toBeInTheDocument();
  });

  it("switching a field's type to date calls onChange with the new type", () => {
    const onChange = vi.fn();
    render(
      <StartPanel
        data={{
          inputVariables: [{ name: "x", type: "text", required: false, description: "" }],
        }}
        onChange={onChange}
      />
    );
    fireEvent.change(screen.getByDisplayValue("text"), { target: { value: "date" } });
    expect(onChange).toHaveBeenCalledWith({
      inputVariables: [{ name: "x", type: "date", required: false, description: "" }],
      inputs: undefined,
    });
  });
});
