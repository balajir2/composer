import { describe, it, expect, vi, beforeEach } from "vitest";

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock("./client", () => ({ apiFetch }));

import { listExecutions } from "./executions";

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
});
