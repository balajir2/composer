import { request } from "@playwright/test";
import crypto from "crypto";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

export interface TestUser {
  email: string;
  password: string;
  id: string;
  accessToken: string;
}

// Every user createTestUser() registers gets tracked here so a single
// `test.afterEach(async () => cleanupTestUsers())` per spec file can
// hard-delete it (and everything it owns) afterward, via
// DELETE /internal/test-users/me (src/api/test_cleanup.py). Without this,
// Playwright runs left orphaned pw-*@example.com accounts and workflows in
// the shared dev database forever — 21 of them accumulated between
// 2026-07-10 and 2026-07-20 before this existed, and had to be cleaned up
// by hand.
const createdUsers: TestUser[] = [];

export async function createTestUser(): Promise<TestUser> {
  const email = `pw-${crypto.randomBytes(4).toString("hex")}@example.com`;
  const password = "correct-horse-battery-staple";
  const ctx = await request.newContext();
  const r = await ctx.post(`${apiUrl}/auth/register`, {
    data: { email, password, displayName: "Playwright" },
  });
  if (r.status() !== 201) {
    throw new Error(`register failed: ${r.status()} ${await r.text()}`);
  }
  const body = (await r.json()) as { id: string; accessToken: string };
  await ctx.dispose();
  const user: TestUser = {
    email,
    password,
    id: body.id,
    accessToken: body.accessToken,
  };
  createdUsers.push(user);
  return user;
}

/**
 * Hard-delete a single test user (and everything it owns) directly — for
 * specs that obtain a user's token without going through createTestUser()
 * above (nothing currently does, but this is the primitive both this file
 * and any such spec should use).
 */
export async function deleteTestUser(user: Pick<TestUser, "email" | "accessToken">): Promise<void> {
  const ctx = await request.newContext();
  try {
    const r = await ctx.delete(`${apiUrl}/internal/test-users/me`, {
      headers: { Authorization: `Bearer ${user.accessToken}` },
    });
    // 204 = deleted. 404 = already gone (e.g. a test that deleted its own
    // account as part of the scenario). Anything else means the backend
    // refused (wrong environment, email didn't match the test pattern) —
    // warn loudly rather than silently leaking the account, but don't fail
    // an otherwise-passing test over cleanup.
    if (r.status() !== 204 && r.status() !== 404) {
      // eslint-disable-next-line no-console
      console.warn(
        `deleteTestUser: failed to delete ${user.email}: ${r.status()} ${await r.text()}`
      );
    }
  } finally {
    await ctx.dispose();
  }
}

/**
 * Hard-delete every user createTestUser() has registered since the last
 * call. Call this from a `test.afterEach` in every spec that uses
 * createTestUser() — see this file's module comment above.
 */
export async function cleanupTestUsers(): Promise<void> {
  const users = createdUsers.splice(0, createdUsers.length);
  for (const user of users) {
    await deleteTestUser(user);
  }
}
