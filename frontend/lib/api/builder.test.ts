import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock("./client", () => ({ apiFetch }));

import { sendBuilderMessage } from "./builder";

const okResponse = {
  sessionId: "s1",
  events: [],
  reply: "ok",
  workflow: {},
  validation_errors: [],
};

describe("sendBuilderMessage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiFetch.mockResolvedValue(okResponse);
  });

  it("posts to the builder endpoint", async () => {
    const res = await sendBuilderMessage("w1", { message: "hi", sessionId: "s1" });
    expect(res.sessionId).toBe("s1");
  });

  it("POSTs the JSON body to /builder/{workflowId}/message", async () => {
    await sendBuilderMessage("w1", { message: "hi", sessionId: "s1" });
    expect(apiFetch).toHaveBeenCalledWith("/builder/w1/message", {
      method: "POST",
      body: JSON.stringify({ message: "hi", sessionId: "s1" }),
    });
  });

  it("passes through a null workflow from an expired session", async () => {
    apiFetch.mockResolvedValueOnce({
      sessionId: "s-old",
      events: [{ type: "session_expired" }],
      reply: "This build session has expired. Please start over.",
      workflow: null,
      validation_errors: [],
    });
    const res = await sendBuilderMessage("w1", { message: "hi", sessionId: "s-old" });
    expect(res.workflow).toBeNull();
    expect(res.events).toEqual([{ type: "session_expired" }]);
  });
});
