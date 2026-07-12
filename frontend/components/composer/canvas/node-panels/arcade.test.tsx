import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ArcadePanel from "./arcade";

describe("ArcadePanel", () => {
  it("renders canonical field values", () => {
    const onChange = vi.fn();
    render(
      <ArcadePanel
        data={{
          arcadeTool: "Google.CreateDocument",
          arcadeInput: { title: "Hello" },
          arcadeUserId: "{{user_email}}",
        }}
        onChange={onChange}
        currentNodeId="arcade-1"
      />
    );
    expect(screen.getByLabelText("Tool name")).toHaveValue("Google.CreateDocument");
    expect(screen.getByLabelText(/User ID/)).toHaveValue("{{user_email}}");
  });

  it("editing the tool name calls onChange with arcadeTool, not toolName", () => {
    const onChange = vi.fn();
    render(<ArcadePanel data={{}} onChange={onChange} currentNodeId="arcade-1" />);
    fireEvent.change(screen.getByLabelText("Tool name"), {
      target: { value: "Slack.SendMessage" },
    });
    expect(onChange).toHaveBeenCalledWith({ arcadeTool: "Slack.SendMessage" });
  });

  it("entering valid JSON args calls onChange with a parsed arcadeInput object", () => {
    const onChange = vi.fn();
    render(<ArcadePanel data={{}} onChange={onChange} currentNodeId="arcade-1" />);
    fireEvent.change(screen.getByLabelText(/^Args/), {
      target: { value: '{"n_emails": 5}' },
    });
    expect(onChange).toHaveBeenCalledWith({ arcadeInput: { n_emails: 5 } });
  });

  it("entering invalid JSON args shows an inline error and does not call onChange with arcadeInput", () => {
    const onChange = vi.fn();
    render(<ArcadePanel data={{}} onChange={onChange} currentNodeId="arcade-1" />);
    fireEvent.change(screen.getByLabelText(/^Args/), {
      target: { value: "{not valid" },
    });
    expect(screen.getByText(/Invalid JSON/)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalledWith(
      expect.objectContaining({ arcadeInput: expect.anything() })
    );
  });

  it("editing the user ID field calls onChange with arcadeUserId", () => {
    const onChange = vi.fn();
    render(<ArcadePanel data={{}} onChange={onChange} currentNodeId="arcade-1" />);
    fireEvent.change(screen.getByLabelText(/User ID/), {
      target: { value: "{{input.user_email}}" },
    });
    expect(onChange).toHaveBeenCalledWith({ arcadeUserId: "{{input.user_email}}" });
  });

  it("migrates legacy toolName/args fields to canonical names on load", () => {
    const onChange = vi.fn();
    render(
      <ArcadePanel
        data={{ toolName: "Google.CreateDocument", args: '{"title": "Hi"}' }}
        onChange={onChange}
        currentNodeId="arcade-1"
      />
    );
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        arcadeTool: "Google.CreateDocument",
        arcadeInput: { title: "Hi" },
        toolName: undefined,
        args: undefined,
      })
    );
  });

  it("does not overwrite an already-canonical field with an empty legacy value", () => {
    const onChange = vi.fn();
    render(
      <ArcadePanel
        data={{ arcadeTool: "Google.CreateDocument", toolName: "Stale.Tool" }}
        onChange={onChange}
        currentNodeId="arcade-1"
      />
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Tool name")).toHaveValue("Google.CreateDocument");
  });

  it("shows guidance that the node pauses for OAuth and requires ARCADE_API_KEY", () => {
    const onChange = vi.fn();
    render(<ArcadePanel data={{}} onChange={onChange} currentNodeId="arcade-1" />);
    expect(screen.getByText(/ARCADE_API_KEY/)).toBeInTheDocument();
    expect(screen.getByText(/pause/i)).toBeInTheDocument();
  });
});
