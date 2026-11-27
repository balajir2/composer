import { describe, it, expect, vi, beforeEach } from "vitest";

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock("./client", () => ({ apiFetch }));

import { getExecutionObservability, listExecutionAttempts, listExecutions } from "./executions";

beforeEach(() => {
  vi.clearAllMocks();
  apiFetch.mockResolvedValue({ total: 0, items: [], limit: 50, offset: 0 });
});

describe("listExecutions", () => {
  it("sends workflowId as the query key the backend actually binds", async () => {
    await listExecutions({ workflowId: "wf-1", limit: 1 });
    expect(apiFetch).toHaveBeenCalledWith("/executions?workflowId=wf-1&limit=1");
  });

  it("omits the query string entirely when called with no params", async () => {
    await listExecutions();
    expect(apiFetch).toHaveBeenCalledWith("/executions");
  });

  it("serializes status as a history filter", async () => {
    await listExecutions({ status: "failed" });
    expect(apiFetch).toHaveBeenCalledWith("/executions?status=failed");
  });
});

describe("execution observability reads", () => {
  it("fetches the lifecycle summary", async () => {
    await getExecutionObservability("e-1");
    expect(apiFetch).toHaveBeenCalledWith("/executions/e-1/observability");
  });

  it("serializes attempt pagination", async () => {
    await listExecutionAttempts("e-1", { limit: 25, offset: 50 });
    expect(apiFetch).toHaveBeenCalledWith("/executions/e-1/attempts?limit=25&offset=50");
  });

  it("omits the query string when attempt pagination is absent", async () => {
    await listExecutionAttempts("e-1");
    expect(apiFetch).toHaveBeenCalledWith("/executions/e-1/attempts");
  });
});
