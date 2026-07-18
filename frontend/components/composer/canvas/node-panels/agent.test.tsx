import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AgentPanel from "./agent";

const { getCatalog, listEnabledLlmModels } = vi.hoisted(() => ({
  getCatalog: vi.fn(),
  listEnabledLlmModels: vi.fn(),
}));

vi.mock("@/lib/api/catalog", () => ({ getCatalog }));
vi.mock("@/lib/api/llm-models", () => ({ listEnabledLlmModels }));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  getCatalog.mockResolvedValue([]);
  listEnabledLlmModels.mockResolvedValue([
    { id: "m1", provider: "anthropic", modelId: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5", enabled: true, verificationStatus: null, verificationMessage: null, verifiedAt: null, createdAt: "", updatedAt: "" },
  ]);
});

describe("AgentPanel — provider inference from stored model", () => {
  it("shows 'Select a provider first' when neither provider nor model is set (brand-new node)", async () => {
    render(wrap(<AgentPanel data={{}} onChange={vi.fn()} />));
    expect(await screen.findByText("Select a provider first.")).toBeInTheDocument();
    const providerSelect = screen.getByLabelText("Provider") as HTMLSelectElement;
    expect(providerSelect.value).toBe("");
  });

  it(
    "infers and shows the provider from a template-seeded model string even though " +
      "`provider` was never explicitly set (the reported bug: node looked unconfigured " +
      "but was already valid and ran fine)",
    async () => {
      render(
        wrap(
          <AgentPanel
            data={{ model: "anthropic/claude-haiku-4-5-20251001" }}
            onChange={vi.fn()}
          />
        )
      );
      const providerSelect = (await screen.findByLabelText("Provider")) as HTMLSelectElement;
      expect(providerSelect.value).toBe("anthropic");

      await waitFor(() => expect(listEnabledLlmModels).toHaveBeenCalledWith("anthropic"));

      const modelSelect = (await screen.findByLabelText("Model")) as HTMLSelectElement;
      expect(modelSelect.value).toBe("anthropic/claude-haiku-4-5-20251001");
    }
  );

  it("prefers an explicitly-stored provider over inferring one from model", async () => {
    render(
      wrap(
        <AgentPanel
          data={{ provider: "openai", model: "anthropic/claude-haiku-4-5-20251001" }}
          onChange={vi.fn()}
        />
      )
    );
    const providerSelect = (await screen.findByLabelText("Provider")) as HTMLSelectElement;
    expect(providerSelect.value).toBe("openai");
  });

  it("leaves provider empty when model has no '/' (malformed/legacy bare modelId, no crash)", async () => {
    render(wrap(<AgentPanel data={{ model: "claude-haiku-4-5-20251001" }} onChange={vi.fn()} />));
    const providerSelect = (await screen.findByLabelText("Provider")) as HTMLSelectElement;
    expect(providerSelect.value).toBe("");
  });
});
