import { request } from "@playwright/test";
import crypto from "crypto";

const apiUrl = process.env.NEXT_PUBLIC_COMPOSER_API_URL ?? "http://localhost:8000";

export interface TestUser {
  email: string;
  password: string;
  id: string;
  accessToken: string;
}

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
  return {
    email,
    password,
    id: body.id,
    accessToken: body.accessToken,
  };
}
