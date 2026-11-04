import { apiFetch } from "./client";

export type BuilderEvent =
  | { type: "plan"; stages: Array<{ id: string; summary: string }> }
  | { type: "stage_applied"; node_count: number; edge_count: number }
  | {
      type: "credential_request";
      kind: string;
      node_id: string;
      detail: string;
      resolvable_by: "user" | "admin";
    }
  | { type: "patch_proposal"; diff: unknown; explanation: string }
  | { type: "message"; content: string }
  | { type: "error"; detail: string }
  | { type: "session_expired" };

export type BuilderMessageRequest = {
  message: string;
  sessionId?: string;
  executionId?: string;
  /** "provider/modelId"; must be one the admin allowed. Omit for the default. */
  model?: string;
};

export type BuilderModelOption = { id: string; label: string };
export type BuilderModels = { default: string; models: BuilderModelOption[] };

/** Models the signed-in user may pick in the Build-with-AI panel. */
export async function listBuilderModels(): Promise<BuilderModels> {
  return apiFetch<BuilderModels>("/builder/models");
}

export type BuilderModelSettings = { defaultModel: string; allowedModels: string[] };

/** Admin: the default builder model and the full allow-list (default included). */
export async function adminGetBuilderModels(): Promise<BuilderModelSettings> {
  return apiFetch<BuilderModelSettings>("/admin/builder-models");
}

export async function adminUpdateBuilderModels(
  body: BuilderModelSettings
): Promise<BuilderModelSettings> {
  return apiFetch<BuilderModelSettings>("/admin/builder-models", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export type BuilderMessageResponse = {
  sessionId: string;
  events: BuilderEvent[];
  reply: string;
  // `null` on the `session_expired` response: the backend returns
  // `workflow=None` when the supplied sessionId is unknown/expired, so
  // consumers must guard before reading `id` / `nodes` / `edges`.
  workflow: {
    id: string;
    nodes: unknown[];
    edges: unknown[];
    [key: string]: unknown;
  } | null;
  validation_errors: string[];
};

export async function sendBuilderMessage(
  workflowId: string,
  body: BuilderMessageRequest
): Promise<BuilderMessageResponse> {
  return apiFetch<BuilderMessageResponse>(`/builder/${workflowId}/message`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
