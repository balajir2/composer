import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock global fetch for all tests in this file.
const mockFetch = vi.fn();
beforeEach(() => {
  globalThis.fetch = mockFetch as never;
});
afterEach(() => {
  vi.resetAllMocks();
});

describe("composer session helpers", () => {
  it("composerRefresh returns tokens on success", async () => {
    const nowSec = Math.floor(Date.now() / 1000);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        accessToken: "a2",
        refreshToken: "r2",
        accessTokenExpiresAt: nowSec + 28800,
        refreshTokenExpiresAt: nowSec + 2592000,
      }),
    });
    const { composerRefresh } = await import("@/lib/composer-api");
    const result = await composerRefresh("r1");
    expect(result.accessToken).toBe("a2");
    expect(result.accessTokenExpiresAt).toBe(nowSec + 28800);
    expect(result.refreshTokenExpiresAt).toBe(nowSec + 2592000);
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

  it("composerLogin falls back to JWT exp claim when backend omits expiry fields", async () => {
    // Craft a JWT with exp = nowSec + 3600.
    const nowSec = Math.floor(Date.now() / 1000);
    const exp = nowSec + 3600;
    const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }))
      .replace(/=/g, "")
      .replace(/\+/g, "-")
      .replace(/\//g, "_");
    const payload = btoa(JSON.stringify({ sub: "u1", exp }))
      .replace(/=/g, "")
      .replace(/\+/g, "-")
      .replace(/\//g, "_");
    const fakeJwt = `${header}.${payload}.sig`;

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        accessToken: fakeJwt,
        refreshToken: fakeJwt,
        // no *ExpiresAt fields — test falls back to JWT decoding
      }),
    });
    const { composerLogin } = await import("@/lib/composer-api");
    const result = await composerLogin("u@example.com", "pw");
    expect(result.accessTokenExpiresAt).toBe(exp);
    expect(result.refreshTokenExpiresAt).toBe(exp);
  });
});
