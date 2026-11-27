import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ComposerApiError } from "@/lib/api/client";
import { PublishDialog } from "./publish-dialog";

const MESSAGE =
  'User approval step "approval-1" has no "rejected" path. Connect its "rejected" handle to a step (for example End).';

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/api/workflows", () => ({
  getWorkflow: vi.fn().mockResolvedValue({
    id: "wf1",
    name: "Flow",
    tags: [],
    nodes: [],
    edges: [],
    isTemplate: false,
    isPublic: false,
  }),
  updateWorkflow: vi.fn().mockRejectedValue(
    new ComposerApiError(422, {
      detail:
        'User approval step "approval-1" has no "rejected" path. Connect its "rejected" handle to a step (for example End).',
    })
  ),
}));

describe("PublishDialog", () => {
  it("keeps the server's refusal on screen", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PublishDialog
          open={true}
          onOpenChange={vi.fn()}
          workflow={{ id: "wf1", name: "Flow", externalSlug: null } as never}
        />
      </QueryClientProvider>
    );
    fireEvent.click(screen.getByRole("button", { name: /publish/i, hidden: false }));
    const alert = await waitFor(() => screen.getByTestId("publish-refused"));
    expect(alert.textContent).toBe(MESSAGE);
  });
});
