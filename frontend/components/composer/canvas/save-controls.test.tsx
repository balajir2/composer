import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SaveControls } from "./save-controls";

vi.mock("@/lib/api/executions", () => ({ createExecution: vi.fn() }));
vi.mock("@/lib/api/uploads", () => ({ extractDocumentText: vi.fn() }));

const MESSAGE =
  'User approval step "approval-1" has no "rejected" path. Connect its "rejected" handle to a step (for example End).';

function renderControls(warnings?: Parameters<typeof SaveControls>[0]["warnings"]) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <SaveControls
        workflowId="wf1"
        saveStatus="saved"
        onSave={vi.fn()}
        getCurrentNodes={() => []}
        onExecutionStarted={vi.fn()}
        warnings={warnings}
      />
    </QueryClientProvider>
  );
}

describe("SaveControls warnings", () => {
  it("summarises the server's warnings next to Save / Run", () => {
    renderControls([
      { code: "missing_branch", nodeId: "approval-1", branch: "rejected", message: MESSAGE },
    ]);
    const box = screen.getByTestId("save-warnings");
    expect(box).toHaveTextContent("1 problem");
    expect(box).toHaveTextContent(MESSAGE);
  });

  it("shows nothing when there are no warnings", () => {
    renderControls([]);
    expect(screen.queryByTestId("save-warnings")).toBeNull();
  });
});
