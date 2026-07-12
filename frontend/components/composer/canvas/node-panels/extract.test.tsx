import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ExtractPanel from "./extract";

describe("ExtractPanel", () => {
  it("editing the input field calls onChange with input, not inputVariable", () => {
    const onChange = vi.fn();
    render(<ExtractPanel data={{}} onChange={onChange} currentNodeId="ex-1" />);
    fireEvent.change(screen.getByLabelText(/^Input/), {
      target: { value: "{{lastOutput}}" },
    });
    expect(onChange).toHaveBeenCalledWith({ input: "{{lastOutput}}" });
  });

  it("entering valid JSON schema calls onChange with a parsed jsonSchema object", () => {
    const onChange = vi.fn();
    render(<ExtractPanel data={{}} onChange={onChange} currentNodeId="ex-1" />);
    fireEvent.change(screen.getByLabelText(/Output schema/), {
      target: { value: '{"name": "string"}' },
    });
    expect(onChange).toHaveBeenCalledWith({ jsonSchema: { name: "string" } });
  });

  it("entering invalid JSON schema shows an inline error and does not call onChange with jsonSchema", () => {
    const onChange = vi.fn();
    render(<ExtractPanel data={{}} onChange={onChange} currentNodeId="ex-1" />);
    fireEvent.change(screen.getByLabelText(/Output schema/), {
      target: { value: "{bad json" },
    });
    expect(screen.getByText(/Invalid JSON/)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalledWith(
      expect.objectContaining({ jsonSchema: expect.anything() })
    );
  });

  it("migrates legacy inputVariable/schema fields to canonical names on load", () => {
    const onChange = vi.fn();
    render(
      <ExtractPanel
        data={{ inputVariable: "state.text", schema: '{"a": "string"}' }}
        onChange={onChange}
        currentNodeId="ex-1"
      />
    );
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        input: "state.text",
        jsonSchema: { a: "string" },
        inputVariable: undefined,
        schema: undefined,
      })
    );
  });
});
