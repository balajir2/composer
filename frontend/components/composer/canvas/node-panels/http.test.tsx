import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import HttpPanel from "./http";

describe("HttpPanel", () => {
  it("renders canonical field values", () => {
    const onChange = vi.fn();
    render(
      <HttpPanel
        data={{
          httpMethod: "POST",
          httpUrl: "https://api.example.com/endpoint",
          httpHeaders: { Authorization: "Bearer {{token}}" },
          httpBody: { key: "{{value}}" },
          responsePath: "data.id",
        }}
        onChange={onChange}
        currentNodeId="http-1"
      />
    );
    expect(screen.getByLabelText("URL")).toHaveValue("https://api.example.com/endpoint");
    expect(screen.getByLabelText(/Response path/)).toHaveValue("data.id");
  });

  it("editing the URL field calls onChange with httpUrl, not url", () => {
    const onChange = vi.fn();
    render(<HttpPanel data={{}} onChange={onChange} currentNodeId="http-1" />);
    fireEvent.change(screen.getByLabelText("URL"), {
      target: { value: "https://x.example.com" },
    });
    expect(onChange).toHaveBeenCalledWith({ httpUrl: "https://x.example.com" });
  });

  it("editing the method select calls onChange with httpMethod, not method", () => {
    const onChange = vi.fn();
    render(<HttpPanel data={{}} onChange={onChange} currentNodeId="http-1" />);
    fireEvent.change(screen.getByLabelText("Method"), { target: { value: "POST" } });
    expect(onChange).toHaveBeenCalledWith({ httpMethod: "POST" });
  });

  it("entering valid JSON headers calls onChange with a parsed httpHeaders object", () => {
    const onChange = vi.fn();
    render(<HttpPanel data={{}} onChange={onChange} currentNodeId="http-1" />);
    fireEvent.change(screen.getByLabelText(/^Headers/), {
      target: { value: '{"Authorization": "Bearer x"}' },
    });
    expect(onChange).toHaveBeenCalledWith({ httpHeaders: { Authorization: "Bearer x" } });
  });

  it("entering invalid JSON headers shows an inline error and does not call onChange with httpHeaders", () => {
    const onChange = vi.fn();
    render(<HttpPanel data={{}} onChange={onChange} currentNodeId="http-1" />);
    fireEvent.change(screen.getByLabelText(/^Headers/), {
      target: { value: "{not valid json" },
    });
    expect(screen.getByText(/Invalid JSON/)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalledWith(
      expect.objectContaining({ httpHeaders: expect.anything() })
    );
  });

  it("entering valid JSON in the body field (JSON mode) calls onChange with parsed httpBody", () => {
    const onChange = vi.fn();
    render(<HttpPanel data={{}} onChange={onChange} currentNodeId="http-1" />);
    fireEvent.change(screen.getByLabelText("Body"), {
      target: { value: '{"key": "value"}' },
    });
    expect(onChange).toHaveBeenCalledWith({ httpBody: { key: "value" } });
  });

  it("switching body mode to Raw text and typing calls onChange with the raw string httpBody", () => {
    const onChange = vi.fn();
    render(<HttpPanel data={{}} onChange={onChange} currentNodeId="http-1" />);
    fireEvent.change(screen.getByLabelText("Body mode"), { target: { value: "raw" } });
    fireEvent.change(screen.getByLabelText("Body"), {
      target: { value: "not-json-at-all" },
    });
    expect(onChange).toHaveBeenCalledWith({ httpBody: "not-json-at-all" });
  });

  it("editing the response path field calls onChange with responsePath", () => {
    const onChange = vi.fn();
    render(<HttpPanel data={{}} onChange={onChange} currentNodeId="http-1" />);
    fireEvent.change(screen.getByLabelText(/Response path/), {
      target: { value: "data.items.0.id" },
    });
    expect(onChange).toHaveBeenCalledWith({ responsePath: "data.items.0.id" });
  });

  it("migrates legacy method/url/headers/body fields to canonical names on load", () => {
    const onChange = vi.fn();
    render(
      <HttpPanel
        data={{
          method: "PUT",
          url: "https://legacy.example.com",
          headers: '{"X-Legacy": "1"}',
          body: '{"a": 1}',
        }}
        onChange={onChange}
        currentNodeId="http-1"
      />
    );
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        httpMethod: "PUT",
        httpUrl: "https://legacy.example.com",
        httpHeaders: { "X-Legacy": "1" },
        httpBody: { a: 1 },
        method: undefined,
        url: undefined,
        headers: undefined,
        body: undefined,
      })
    );
  });

  it("does not overwrite an already-canonical field with an empty legacy value", () => {
    const onChange = vi.fn();
    render(
      <HttpPanel
        data={{ httpUrl: "https://canonical.example.com", url: "https://stale-legacy.example.com" }}
        onChange={onChange}
        currentNodeId="http-1"
      />
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByLabelText("URL")).toHaveValue("https://canonical.example.com");
  });
});
