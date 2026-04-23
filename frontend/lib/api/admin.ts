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
