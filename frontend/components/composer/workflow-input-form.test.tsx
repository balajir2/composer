import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { components } from "@/lib/api/generated/schema";
import { WorkflowInputForm } from "./workflow-input-form";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api/executions", () => ({
  createExecution: vi.fn().mockResolvedValue({ id: "exec1" }),
}));

function wrap(children: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

type Workflow = components["schemas"]["WorkflowRead"];

const baseWf: Workflow = {
  id: "wf1",
  name: "Test",
  description: null,
  nodes: [],
  edges: [],
  isTemplate: false,
  isPublic: false,
  isProduction: false,
  createdAt: null,
  updatedAt: null,
};

describe("WorkflowInputForm", () => {
  it("renders default single-JSON field when no Start inputs declared", () => {
    render(wrap(<WorkflowInputForm workflow={baseWf} />));
    expect(screen.getByLabelText(/Input \(JSON\)/)).toBeInTheDocument();
  });

  it("renders each declared field with the right type", () => {
    const wf: Workflow = {
      ...baseWf,
      nodes: [
        {
          type: "start",
          data: {
            inputs: [
              { name: "topic", label: "Topic", type: "text", required: true },
              { name: "count", label: "Count", type: "number", required: false },
            ],
          },
        },
      ],
    };
    render(wrap(<WorkflowInputForm workflow={wf} />));
    expect(screen.getByLabelText(/Topic/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Count/)).toBeInTheDocument();
  });

  it("shows validation error when required text is empty", async () => {
    const wf: Workflow = {
      ...baseWf,
      nodes: [
        {
          type: "start",
          data: { inputs: [{ name: "topic", label: "Topic", type: "text", required: true }] },
        },
      ],
    };
    render(wrap(<WorkflowInputForm workflow={wf} />));
    fireEvent.click(screen.getByText(/Run workflow/));
    expect(await screen.findByText(/required/i)).toBeInTheDocument();
  });

  it("renders a date picker trigger button (not a plain text input) for a date-typed field", () => {
    const wf: Workflow = {
      ...baseWf,
      nodes: [
        {
          type: "start",
          data: {
            inputVariables: [
              { name: "report_date", description: "Report date", type: "date", required: true },
            ],
          },
        },
      ],
    };
    render(wrap(<WorkflowInputForm workflow={wf} />));
    expect(screen.getByLabelText(/Report date/).tagName).toBe("BUTTON");
  });

  it("renders a date+time picker trigger button for a datetime-typed field", () => {
    const wf: Workflow = {
      ...baseWf,
      nodes: [
        {
          type: "start",
          data: {
            inputVariables: [
              {
                name: "extract_timestamp",
                description: "Extract timestamp",
                type: "datetime",
                required: true,
              },
            ],
          },
        },
      ],
    };
    render(wrap(<WorkflowInputForm workflow={wf} />));
    expect(screen.getByLabelText(/Extract timestamp/).tagName).toBe("BUTTON");
  });
});
