import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock global fetch for all tests in this file.
const mockFetch = vi.fn();
beforeEach(() => {
  globalThis.fetch = mockFetch as never;
});
afterEach(() => {
  vi.resetAllMocks();
});

// composerRefresh lives in composer-api.ts — no NextAuth dependency, safe to
// import in vitest without mocking the next/server module chain.

describe("composer session helpers", () => {
  it("composerRefresh returns tokens on success", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ accessToken: "a2", refreshToken: "r2" }),
    });
    const { composerRefresh } = await import("@/lib/composer-api");
    const result = await composerRefresh("r1");
    expect(result.accessToken).toBe("a2");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/refresh"),
      expect.objectContaining({ method: "POST" })
    );
  });

  it("composerRefresh throws on non-OK response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });
    const { composerRefresh } = await import("@/lib/composer-api");
    await expect(composerRefresh("r1")).rejects.toThrow(/refresh failed/);
  });
});
