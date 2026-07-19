import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ConfluencePanel from "./confluence";

describe("ConfluencePanel", () => {
  it("defaults to create_or_update_page and shows its fields", () => {
    render(<ConfluencePanel data={{}} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Space key")).toBeInTheDocument();
    expect(screen.queryByLabelText("Page ID")).not.toBeInTheDocument();
  });

  it("switching operation to get_property reveals pageId/propertyKey fields", () => {
    render(
      <ConfluencePanel data={{ operation: "get_property" }} onChange={vi.fn()} />
    );
    expect(screen.getByLabelText("Page ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Property key")).toBeInTheDocument();
    expect(screen.queryByLabelText("Space key")).not.toBeInTheDocument();
  });

  it("editing spaceKey calls onChange with spaceKey", () => {
    const onChange = vi.fn();
    render(<ConfluencePanel data={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Space key"), {
      target: { value: "MB" },
    });
    expect(onChange).toHaveBeenCalledWith({ spaceKey: "MB" });
  });

  it("editing the comma-separated labels list calls onChange with an array", () => {
    const onChange = vi.fn();
    render(<ConfluencePanel data={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Labels"), {
      target: { value: "weekly-report, adobe-target" },
    });
    expect(onChange).toHaveBeenCalledWith({ labels: ["weekly-report", "adobe-target"] });
  });

  it("editing propertyKey in get_property mode calls onChange with propertyKey", () => {
    const onChange = vi.fn();
    render(
      <ConfluencePanel data={{ operation: "get_property" }} onChange={onChange} />
    );
    fireEvent.change(screen.getByLabelText("Property key"), {
      target: { value: "metrics_snapshot" },
    });
    expect(onChange).toHaveBeenCalledWith({ propertyKey: "metrics_snapshot" });
  });

  it("switching operation to set_property reveals pageId/propertyKey/propertyValue and hides spaceKey/title", () => {
    render(
      <ConfluencePanel data={{ operation: "set_property" }} onChange={vi.fn()} />
    );
    expect(screen.getByLabelText("Page ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Property key")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Property value (JSON or text)")
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Space key")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Title")).not.toBeInTheDocument();
  });

  it("propertyValue round-trips real JSON as a parsed object, not a string", () => {
    const onChange = vi.fn();
    render(
      <ConfluencePanel data={{ operation: "set_property" }} onChange={onChange} />
    );
    fireEvent.change(screen.getByLabelText("Property value (JSON or text)"), {
      target: { value: '{"total": 42}' },
    });
    expect(onChange).toHaveBeenCalledWith({ propertyValue: { total: 42 } });
  });

  it("preserves a non-JSON template reference as a plain string in propertyValue", () => {
    const onChange = vi.fn();
    render(
      <ConfluencePanel data={{ operation: "set_property" }} onChange={onChange} />
    );
    fireEvent.change(screen.getByLabelText("Property value (JSON or text)"), {
      target: { value: "{{compute_metrics.output}}" },
    });
    expect(onChange).toHaveBeenCalledWith({
      propertyValue: "{{compute_metrics.output}}",
    });
  });

  it("resyncs Labels text when switching to a different node, not just on operation change", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ConfluencePanel
        data={{ labels: ["summary", "status"] }}
        onChange={onChange}
        currentNodeId="node-a"
      />
    );
    expect(screen.getByLabelText("Labels")).toHaveValue("summary, status");

    rerender(
      <ConfluencePanel
        data={{ labels: ["priority"] }}
        onChange={onChange}
        currentNodeId="node-b"
      />
    );
    expect(screen.getByLabelText("Labels")).toHaveValue("priority");
  });
});
