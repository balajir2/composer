import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type LlmKeySummary = components["schemas"]["LlmKeySummary"];
type LlmKeyUpsert = components["schemas"]["LlmKeyUpsert"];

export async function listLlmKeys(): Promise<LlmKeySummary[]> {
  return apiFetch<LlmKeySummary[]>(`/admin/llm-keys`);
}

export async function getLlmKey(provider: string): Promise<LlmKeySummary> {
  return apiFetch<LlmKeySummary>(`/admin/llm-keys/${provider}`);
}

export async function upsertLlmKey(provider: string, body: LlmKeyUpsert): Promise<LlmKeySummary> {
  return apiFetch<LlmKeySummary>(`/admin/llm-keys/${provider}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function deleteLlmKey(provider: string): Promise<void> {
  return apiFetch<void>(`/admin/llm-keys/${provider}`, { method: "DELETE" });
}

export type LlmKeyTestResult = { ok: boolean; status: number | null; message: string };

export async function testLlmKey(provider: string): Promise<LlmKeyTestResult> {
  return apiFetch<LlmKeyTestResult>(`/admin/llm-keys/${provider}/test-connection`, {
    method: "POST",
  });
}

// ── Admin: users ────────────────────────────────────────────────────────────

export type AdminUser = {
  id: string;
  email: string;
  role: "admin" | "member";
  displayName: string | null;
  isActive?: boolean;
};

export async function listAdminUsers(): Promise<AdminUser[]> {
  return apiFetch<AdminUser[]>(`/admin/users`);
}

export async function updateUserRole(id: string, role: "admin" | "member"): Promise<AdminUser> {
  return apiFetch<AdminUser>(`/admin/users/${id}/role`, {
    method: "POST",
    body: JSON.stringify({ role }),
  });
}

export async function deactivateUser(id: string): Promise<AdminUser> {
  return apiFetch<AdminUser>(`/admin/users/${id}`, { method: "DELETE" });
}

export async function reactivateUser(id: string): Promise<AdminUser> {
  return apiFetch<AdminUser>(`/admin/users/${id}/reactivate`, { method: "POST" });
}

export async function resetUserPassword(id: string): Promise<{ temporaryPassword: string }> {
  return apiFetch<{ temporaryPassword: string }>(`/admin/users/${id}/reset-password`, {
    method: "POST",
  });
}

// ── Admin: deployment settings ──────────────────────────────────────────────

export type DeploymentSetting = { key: string; value: string };

export async function listDeploymentSettings(): Promise<DeploymentSetting[]> {
  return apiFetch<DeploymentSetting[]>(`/admin/deployment-settings`);
}

export async function upsertDeploymentSetting(
  key: string,
  value: string
): Promise<DeploymentSetting> {
  return apiFetch<DeploymentSetting>(`/admin/deployment-settings/${key}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}

// ── Admin: built-in tools ───────────────────────────────────────────────────

export type BuiltInToolTestResult = {
  ok: boolean;
  message: string;
  has_db_key: boolean;
  has_runtime_key: boolean;
};

export async function testBuiltInTool(toolId: string): Promise<BuiltInToolTestResult> {
  return apiFetch<BuiltInToolTestResult>(`/admin/tools/${toolId}/test-connection`, {
    method: "POST",
  });
}
