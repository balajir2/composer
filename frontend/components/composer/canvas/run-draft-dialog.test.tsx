import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RunDraftDialog } from "./run-draft-dialog";

vi.mock("@/lib/api/executions", () => ({
  createExecution: vi.fn().mockResolvedValue({ id: "exec1" }),
}));

vi.mock("@/lib/api/uploads", () => ({
  extractDocumentText: vi.fn(),
}));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("RunDraftDialog", () => {
  it("renders a file picker for a document-typed Start input", () => {
    const workflow = {
      nodes: [
        {
          type: "start",
          data: {
            inputVariables: [
              {
                name: "requirements_doc",
                type: "document",
                required: true,
                description: "Upload a PDF.",
              },
            ],
          },
        },
      ],
    };
    render(
      wrap(
        <RunDraftDialog
          open={true}
          onOpenChange={vi.fn()}
          workflowId="wf1"
          workflow={workflow}
          onStarted={vi.fn()}
        />
      )
    );
    const fileInput = document.querySelector('input[type="file"]');
    expect(fileInput).not.toBeNull();
    expect(screen.queryByText(/Accepts \.txt, \.md, \.pdf, \.docx/)).toBeInTheDocument();
  });
});
