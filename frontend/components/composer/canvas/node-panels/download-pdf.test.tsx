import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DownloadPdfPanel from "./download-pdf";

vi.mock("./google-drive-connect", () => ({
  default: (props: Record<string, unknown>) => (
    <div data-testid="google-drive-connect" data-props={JSON.stringify(props)} />
  ),
}));

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

  it("shows the destination path field for the local provider by default", () => {
    render(<DownloadPdfPanel data={{}} onChange={vi.fn()} currentNodeId="dp-1" />);
    expect(screen.getByLabelText("Destination path")).toBeInTheDocument();
    expect(screen.queryByTestId("google-drive-connect")).not.toBeInTheDocument();
  });

  it("swaps to the Google Drive picker when provider is google-drive", () => {
    render(
      <DownloadPdfPanel
        data={{ provider: "google-drive", connectionId: "conn-1", driveFolderId: "folder-1" }}
        onChange={vi.fn()}
        currentNodeId="dp-1"
      />
    );
    expect(screen.queryByLabelText("Destination path")).not.toBeInTheDocument();
    const picker = screen.getByTestId("google-drive-connect");
    const props = JSON.parse(picker.getAttribute("data-props") ?? "{}");
    expect(props.connectionId).toBe("conn-1");
    expect(props.driveFolderId).toBe("folder-1");
    expect(props.showProcessedErrorFolders).toBe(false);
  });

  it("selecting google-drive in the provider dropdown calls onChange with provider", () => {
    const onChange = vi.fn();
    render(<DownloadPdfPanel data={{}} onChange={onChange} currentNodeId="dp-1" />);
    fireEvent.change(screen.getByLabelText("Provider"), {
      target: { value: "google-drive" },
    });
    expect(onChange).toHaveBeenCalledWith({ provider: "google-drive" });
  });
});
