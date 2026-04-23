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
