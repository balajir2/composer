import { apiFetch } from "./client";

export interface LlmModelSummary {
  id: string;
  provider: string;
  modelId: string;
  label: string | null;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

/**
 * List enabled models, optionally filtered by provider.
 * Available to any authenticated user (used by the Designer's agent panel).
 */
export async function listEnabledLlmModels(provider?: string) {
  const qs = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  return apiFetch<LlmModelSummary[]>(`/llm-models${qs}`);
}

export interface AvailableModel {
  modelId: string;
  label: string | null;
  source: "live" | "db";
}
export interface AvailableModelsResponse {
  provider: string;
  models: AvailableModel[];
}

/**
 * Ask the backend to fetch the current model list from the provider's API
 * directly (with fallback to the admin LlmModel table on failure).  Used
 * by the Admin → LLM models → Add-model dialog so the dropdown reflects
 * whatever the provider actually supports — no admin seeding required.
 *
 * Pass `refresh: true` to bypass the backend's 5-minute cache, e.g. to
 * pick up a just-released model without waiting for the TTL.
 */
export async function listAvailableModels(provider: string, refresh = false) {
  const params = new URLSearchParams({ provider });
  if (refresh) params.set("refresh", "1");
  return apiFetch<AvailableModelsResponse>(
    `/llm-models/available?${params.toString()}`
  );
}

/** Admin: list ALL models (including disabled). */
export async function adminListLlmModels() {
  return apiFetch<LlmModelSummary[]>(`/admin/llm-models`);
}

/** Admin: add a new model. */
export async function adminCreateLlmModel(body: {
  provider: string;
  modelId: string;
  label?: string | null;
  enabled?: boolean;
}) {
  return apiFetch<LlmModelSummary>(`/admin/llm-models`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Admin: update label / enabled. */
export async function adminUpdateLlmModel(
  id: string,
  body: { label?: string | null; enabled?: boolean }
) {
  return apiFetch<LlmModelSummary>(`/admin/llm-models/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** Admin: delete permanently. */
export async function adminDeleteLlmModel(id: string) {
  return apiFetch<void>(`/admin/llm-models/${id}`, { method: "DELETE" });
}
