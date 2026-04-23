import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type WorkflowRead = components["schemas"]["WorkflowRead"];
type WorkflowCreate = components["schemas"]["WorkflowCreate"];
type WorkflowListResponse = components["schemas"]["WorkflowListResponse"];

export async function listWorkflows(params?: {
  mine?: boolean;
  limit?: number;
  offset?: number;
}): Promise<WorkflowListResponse> {
  const q = new URLSearchParams();
  if (params?.mine) q.set("mine", "true");
  if (params?.limit !== undefined) q.set("limit", String(params.limit));
  if (params?.offset !== undefined) q.set("offset", String(params.offset));
  const qs = q.toString();
  return apiFetch<WorkflowListResponse>(`/workflows${qs ? `?${qs}` : ""}`);
}

export async function getWorkflow(id: string): Promise<WorkflowRead> {
  return apiFetch<WorkflowRead>(`/workflows/${id}`);
}

export async function createWorkflow(body: WorkflowCreate): Promise<WorkflowRead> {
  return apiFetch<WorkflowRead>(`/workflows`, { method: "POST", body: JSON.stringify(body) });
}

export async function updateWorkflow(id: string, body: WorkflowCreate): Promise<WorkflowRead> {
  return apiFetch<WorkflowRead>(`/workflows/${id}`, { method: "PUT", body: JSON.stringify(body) });
}

export async function deleteWorkflow(id: string): Promise<void> {
  return apiFetch<void>(`/workflows/${id}`, { method: "DELETE" });
}

export async function reassignWorkflowOwner(
  workflowId: string,
  body: { userId?: string; email?: string }
): Promise<WorkflowRead> {
  return apiFetch<WorkflowRead>(`/workflows/${workflowId}/owner`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
