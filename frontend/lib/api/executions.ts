import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type ExecutionRead = components["schemas"]["ExecutionRead"];
type ExecutionCreate = components["schemas"]["ExecutionCreate"];
type ExecutionListResponse = components["schemas"]["ExecutionListResponse"];

export async function listExecutions(params?: {
  workflowId?: string;
  limit?: number;
  offset?: number;
}): Promise<ExecutionListResponse> {
  const q = new URLSearchParams();
  if (params?.workflowId) q.set("workflow_id", params.workflowId);
  if (params?.limit !== undefined) q.set("limit", String(params.limit));
  if (params?.offset !== undefined) q.set("offset", String(params.offset));
  const qs = q.toString();
  return apiFetch<ExecutionListResponse>(`/executions${qs ? `?${qs}` : ""}`);
}

export async function createExecution(body: ExecutionCreate): Promise<ExecutionRead> {
  return apiFetch<ExecutionRead>(`/executions`, { method: "POST", body: JSON.stringify(body) });
}

export async function getExecution(id: string): Promise<ExecutionRead> {
  return apiFetch<ExecutionRead>(`/executions/${id}`);
}

export async function resumeExecution(
  id: string,
  body: { approved: boolean; feedback?: string }
): Promise<ExecutionRead> {
  return apiFetch<ExecutionRead>(`/executions/${id}/resume`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
