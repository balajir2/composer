import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type WorkflowRead = components["schemas"]["WorkflowRead"];
type WorkflowCreate = components["schemas"]["WorkflowCreate"];
type WorkflowListResponse = components["schemas"]["WorkflowListResponse"];

export async function listWorkflows(params?: {
  mine?: boolean;
  isTemplate?: boolean;
  limit?: number;
  offset?: number;
}): Promise<WorkflowListResponse> {
  const q = new URLSearchParams();
  if (params?.mine) q.set("mine", "true");
  if (params?.isTemplate !== undefined)
    q.set("isTemplate", String(params.isTemplate));
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

export async function duplicateWorkflow(id: string): Promise<WorkflowRead> {
  const fetched = await getWorkflow(id);
  return createWorkflow({
    name: fetched.name + " (copy)",
    description: fetched.description ?? null,
    category: fetched.category ?? null,
    tags: fetched.tags ?? [],
    difficulty: fetched.difficulty ?? null,
    estimatedTime: fetched.estimatedTime ?? null,
    nodes: fetched.nodes as WorkflowCreate["nodes"],
    edges: fetched.edges as WorkflowCreate["edges"],
    version: fetched.version ?? null,
    isTemplate: fetched.isTemplate,
    isPublic: fetched.isPublic,
    isProduction: fetched.isProduction ?? null,
    externalSlug: null,
  });
}

/**
 * Clone a template into the current user's workflow list.
 *
 * Differs from `duplicateWorkflow` in that the new workflow is NOT
 * itself a template — designers want a working private copy they can
 * iterate on, not another template clogging the gallery.  Also drops
 * `isPublic` (the template's discoverability is its own; the clone
 * starts private), `isProduction`, and `externalSlug`.
 */
export async function instantiateTemplate(id: string): Promise<WorkflowRead> {
  const fetched = await getWorkflow(id);
  return createWorkflow({
    name: fetched.name,
    description: fetched.description ?? null,
    category: fetched.category ?? null,
    tags: fetched.tags ?? [],
    difficulty: fetched.difficulty ?? null,
    estimatedTime: fetched.estimatedTime ?? null,
    nodes: fetched.nodes as WorkflowCreate["nodes"],
    edges: fetched.edges as WorkflowCreate["edges"],
    version: fetched.version ?? null,
    isTemplate: false,
    isPublic: false,
    isProduction: null,
    externalSlug: null,
  });
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

export async function adminUpdateWorkflowFlags(
  workflowId: string,
  body: { isPublic?: boolean; isProduction?: boolean; externalSlug?: string | null }
): Promise<WorkflowRead> {
  return apiFetch<WorkflowRead>(`/workflows/${workflowId}/admin-flags`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export type WorkflowAssignment = components["schemas"]["WorkflowAssignmentRead"];

export async function listWorkflowAssignments(
  workflowId: string
): Promise<WorkflowAssignment[]> {
  return apiFetch<WorkflowAssignment[]>(`/workflows/${workflowId}/assignments`);
}

export async function assignWorkflowUser(
  workflowId: string,
  userId: string
): Promise<WorkflowAssignment> {
  return apiFetch<WorkflowAssignment>(
    `/workflows/${workflowId}/assignments/${userId}`,
    { method: "POST" }
  );
}

export async function unassignWorkflowUser(
  workflowId: string,
  userId: string
): Promise<void> {
  return apiFetch<void>(`/workflows/${workflowId}/assignments/${userId}`, {
    method: "DELETE",
  });
}
