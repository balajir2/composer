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
    if ("mustChangePassword" in result) throw new Error("unexpected mustChangePassword result");
    expect(result.accessTokenExpiresAt).toBe(exp);
    expect(result.refreshTokenExpiresAt).toBe(exp);
  });

  it("composerLogin returns mustChangePassword variant without normalizing", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        mustChangePassword: true,
        passwordChangeToken: "pc-token-123",
      }),
    });
    const { composerLogin } = await import("@/lib/composer-api");
    const result = await composerLogin("u@example.com", "temp-pass");
    expect(result).toEqual({
      mustChangePassword: true,
      passwordChangeToken: "pc-token-123",
    });
  });

  it("composerChangePassword posts with bearer token and resolves on 204", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204 });
    const { composerChangePassword } = await import("@/lib/composer-api");
    await composerChangePassword("tok-1", "old-pw", "new-pw");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/change-password"),
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer tok-1" }),
      })
    );
  });

  it("composerChangePassword throws on non-OK response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });
    const { composerChangePassword } = await import("@/lib/composer-api");
    await expect(composerChangePassword("tok-1", "wrong", "new-pw")).rejects.toThrow(
      /change password failed/
    );
  });

  it("composerForgotPassword posts email and resolves on 204", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204 });
    const { composerForgotPassword } = await import("@/lib/composer-api");
    await composerForgotPassword("alice@example.com");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/forgot-password"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ email: "alice@example.com" }),
      })
    );
  });

  it("composerForgotPassword does not throw on non-OK (never leak account existence)", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 429 });
    const { composerForgotPassword } = await import("@/lib/composer-api");
    await expect(composerForgotPassword("alice@example.com")).resolves.toBeUndefined();
  });

  it("composerResetPassword posts token and new password, resolves on 204", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204 });
    const { composerResetPassword } = await import("@/lib/composer-api");
    await composerResetPassword("tok-1", "new-pw-12345678");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/reset-password"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ token: "tok-1", newPassword: "new-pw-12345678" }),
      })
    );
  });

  it("composerResetPassword throws on non-OK response", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 400 });
    const { composerResetPassword } = await import("@/lib/composer-api");
    await expect(composerResetPassword("bad-token", "new-pw-12345678")).rejects.toThrow(
      /reset password failed/
    );
  });

  describe("refreshComposerTokens", () => {
    it("re-fetches the current role from /auth/me so a promotion made after login takes effect on the next token refresh, not only on a fresh login", async () => {
      const nowSec = Math.floor(Date.now() / 1000);
      mockFetch
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            accessToken: "new-access",
            refreshToken: "new-refresh",
            accessTokenExpiresAt: nowSec + 28800,
            refreshTokenExpiresAt: nowSec + 2592000,
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            id: "u1",
            email: "apurva@example.com",
            role: "admin",
            displayName: "Apurva Durga",
          }),
        });

      const { refreshComposerTokens } = await import("@/lib/auth-token-refresh");
      const result = await refreshComposerTokens({
        composerRefreshToken: "old-refresh",
        role: "member",
      } as never);

      expect(result.role).toBe("admin");
      expect(result.composerAccessToken).toBe("new-access");
      expect(mockFetch).toHaveBeenLastCalledWith(
        expect.stringContaining("/auth/me"),
        expect.objectContaining({
          headers: expect.objectContaining({ Authorization: "Bearer new-access" }),
        })
      );
    });

    it("keeps the previous role when the /auth/me re-fetch fails, without failing the token refresh itself", async () => {
      const nowSec = Math.floor(Date.now() / 1000);
      mockFetch
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            accessToken: "new-access",
            refreshToken: "new-refresh",
            accessTokenExpiresAt: nowSec + 28800,
            refreshTokenExpiresAt: nowSec + 2592000,
          }),
        })
        .mockResolvedValueOnce({ ok: false, status: 500 });

      const { refreshComposerTokens } = await import("@/lib/auth-token-refresh");
      const result = await refreshComposerTokens({
        composerRefreshToken: "old-refresh",
        role: "admin",
      } as never);

      expect(result.role).toBe("admin");
      expect(result.error).toBeUndefined();
      expect(result.composerAccessToken).toBe("new-access");
    });
  });
});
