import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BuilderModelsCard } from "./builder-models-card";

const { adminGetBuilderModels, adminUpdateBuilderModels, adminListLlmModels } = vi.hoisted(() => ({
  adminGetBuilderModels: vi.fn(),
  adminUpdateBuilderModels: vi.fn(),
  adminListLlmModels: vi.fn(),
}));

vi.mock("@/lib/api/builder", () => ({ adminGetBuilderModels, adminUpdateBuilderModels }));
vi.mock("@/lib/api/llm-models", () => ({ adminListLlmModels }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function model(provider: string, modelId: string, enabled = true) {
  return { id: `${provider}-${modelId}`, provider, modelId, label: null, enabled };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <BuilderModelsCard />
    </QueryClientProvider>
  );
}

describe("BuilderModelsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    adminListLlmModels.mockResolvedValue([
      model("anthropic", "sonnet"),
      model("openai", "gpt-5"),
      model("groq", "llama", false),
    ]);
    adminGetBuilderModels.mockResolvedValue({
      defaultModel: "anthropic/sonnet",
      allowedModels: ["anthropic/sonnet"],
    });
    adminUpdateBuilderModels.mockImplementation(async (b) => b);
  });

  it("lists only enabled catalog models and marks the default as always allowed", async () => {
    renderCard();

    const sonnet = (await screen.findByLabelText("Allow anthropic/sonnet")) as HTMLInputElement;
    expect(sonnet.checked).toBe(true);
    expect(sonnet.disabled).toBe(true); // the default cannot be un-allowed
    expect(screen.getByLabelText("Allow openai/gpt-5")).toBeTruthy();
    expect(screen.queryByLabelText("Allow groq/llama")).toBeNull(); // disabled in the catalog
  });

  it("saves the chosen allow-list", async () => {
    renderCard();

    fireEvent.click(await screen.findByLabelText("Allow openai/gpt-5"));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(adminUpdateBuilderModels).toHaveBeenCalledWith({
        defaultModel: "anthropic/sonnet",
        allowedModels: ["anthropic/sonnet", "openai/gpt-5"],
      })
    );
  });

  it("allows switching the default, which is then always allowed", async () => {
    renderCard();

    fireEvent.click(await screen.findByLabelText("Default openai/gpt-5"));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(adminUpdateBuilderModels).toHaveBeenCalledWith({
        defaultModel: "openai/gpt-5",
        allowedModels: ["openai/gpt-5", "anthropic/sonnet"],
      })
    );
  });

  it("blocks saving while the current default is not in the catalog", async () => {
    adminGetBuilderModels.mockResolvedValue({
      defaultModel: "anthropic/retired",
      allowedModels: ["anthropic/retired"],
    });
    renderCard();

    expect(await screen.findByText(/not in the model catalog/i)).toBeTruthy();
    expect((screen.getByRole("button", { name: /save/i }) as HTMLButtonElement).disabled).toBe(
      true
    );
  });
});
