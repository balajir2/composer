import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type ApiKeyCreateRequest = components["schemas"]["ApiKeyCreateRequest"];
type ApiKeyCreateResponse = components["schemas"]["ApiKeyCreateResponse"];
type ApiKeySummary = components["schemas"]["ApiKeySummary"];

export async function listApiKeys(): Promise<ApiKeySummary[]> {
  return apiFetch<ApiKeySummary[]>(`/api-keys`);
}

export async function createApiKey(body: ApiKeyCreateRequest): Promise<ApiKeyCreateResponse> {
  return apiFetch<ApiKeyCreateResponse>(`/api-keys`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function revokeApiKey(keyId: string): Promise<void> {
  return apiFetch<void>(`/api-keys/${keyId}`, { method: "DELETE" });
}
