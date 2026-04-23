import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type McpServerRead = components["schemas"]["McpServerRead"];
type McpServerCreate = components["schemas"]["McpServerCreate"];

export async function listMcpServers(): Promise<McpServerRead[]> {
  return apiFetch<McpServerRead[]>(`/mcp-servers`);
}

export async function createMcpServer(body: McpServerCreate): Promise<McpServerRead> {
  return apiFetch<McpServerRead>(`/mcp-servers`, { method: "POST", body: JSON.stringify(body) });
}

export async function getMcpServer(serverId: string): Promise<McpServerRead> {
  return apiFetch<McpServerRead>(`/mcp-servers/${serverId}`);
}

export async function deleteMcpServer(serverId: string): Promise<void> {
  return apiFetch<void>(`/mcp-servers/${serverId}`, { method: "DELETE" });
}

export async function setMcpShared(serverId: string, isShared: boolean): Promise<McpServerRead> {
  return apiFetch<McpServerRead>(`/mcp-servers/${serverId}/shared`, {
    method: "PATCH",
    body: JSON.stringify({ isShared }),
  });
}

export async function oauthAuthorize(
  serverId: string,
  body: { redirectUri: string }
): Promise<{ authorizationUrl: string }> {
  return apiFetch<{ authorizationUrl: string }>(`/mcp-servers/${serverId}/oauth/authorize`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
