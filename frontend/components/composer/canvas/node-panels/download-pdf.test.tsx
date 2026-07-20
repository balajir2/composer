import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DownloadPdfPanel from "./download-pdf";

const CONTENT_PLACEHOLDER = "Reference an upstream node's output, e.g. {{narrative_agent}}";

describe("DownloadPdfPanel", () => {
  it("shows the input format, destination, and filename fields", () => {
    render(<DownloadPdfPanel data={{}} onChange={vi.fn()} currentNodeId="dp-1" />);
    expect(screen.getByLabelText("Input format")).toBeInTheDocument();
    expect(screen.getByLabelText("Destination path")).toBeInTheDocument();
    expect(screen.getByLabelText("Filename (no extension)")).toBeInTheDocument();
    // PromptField's <Label> has no htmlFor association with its textarea
    // (see prompt-field.tsx) -- getByLabelText won't find it, but its
    // placeholder text proves it's rendered.
    expect(screen.getByPlaceholderText(CONTENT_PLACEHOLDER)).toBeInTheDocument();
  });

  it("selecting input format calls onChange with inputFormat", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByLabelText("Input format"), {
      target: { value: "markdown" },
    });
    expect(onChange).toHaveBeenCalledWith({ inputFormat: "markdown" });
  });

  it("editing destination path calls onChange with destinationPath", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByLabelText("Destination path"), {
      target: { value: "/out" },
    });
    expect(onChange).toHaveBeenCalledWith({ destinationPath: "/out" });
  });

  it("editing filename calls onChange with filename", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByLabelText("Filename (no extension)"), {
      target: { value: "weekly-report" },
    });
    expect(onChange).toHaveBeenCalledWith({ filename: "weekly-report" });
  });

  it("editing content calls onChange with content", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByPlaceholderText(CONTENT_PLACEHOLDER), {
      target: { value: "<html>report</html>" },
    });
    expect(onChange).toHaveBeenCalledWith({ content: "<html>report</html>" });
  });
});
