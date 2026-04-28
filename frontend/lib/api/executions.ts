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

/**
 * Delete an execution + its derived artefacts (approvals,
 * LangGraph checkpoints).  Authz: owner OR admin.  Members deleting
 * other users' executions get a 404 (existence is hidden).
 */
export async function deleteExecution(id: string): Promise<void> {
  return apiFetch<void>(`/executions/${id}`, { method: "DELETE" });
}

export interface BulkDeleteResponse {
  deletedCount: number;
  skippedCount: number;
}

/**
 * Bulk-delete executions in one round-trip.  Pass either
 * `executionIds` (preferred — what the history-page checkboxes
 * produce) or `allInScope: true` (admin-only "wipe all" mode).
 * Members passing ids they don't own get those silently skipped;
 * `skippedCount` in the response tells you how many didn't apply.
 */
export async function bulkDeleteExecutions(body: {
  executionIds?: string[];
  allInScope?: boolean;
}): Promise<BulkDeleteResponse> {
  return apiFetch<BulkDeleteResponse>(`/executions/delete-bulk`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
