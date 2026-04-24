import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type McpServerRead = components["schemas"]["McpServerRead"];
type McpServerCreate = components["schemas"]["McpServerCreate"];

export async function listMcpServers(): Promise<McpServerRead[]> {
  return apiFetch<McpServerRead[]>(`/mcp-servers`);
}

// Accept oauthConfig as an extra field — the generated schema lags the
// backend until we regen openapi types, and hand-typing the union is fine
// here since the backend validates strictly.
export async function createMcpServer(
  body: McpServerCreate & { oauthConfig?: OauthConfigInput }
): Promise<McpServerRead> {
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
): Promise<{ authorizeUrl: string }> {
  return apiFetch<{ authorizeUrl: string }>(`/mcp-servers/${serverId}/oauth/authorize`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export type OauthConfigInput = {
  authorizeUrl: string;
  tokenUrl: string;
  clientId: string;
  clientSecret?: string | null;
  scopes: string[];
};

export async function updateMcpOauthConfig(
  serverId: string,
  oauthConfig: OauthConfigInput
): Promise<McpServerRead> {
  return apiFetch<McpServerRead>(`/mcp-servers/${serverId}/oauth-config`, {
    method: "PATCH",
    body: JSON.stringify({ oauthConfig }),
  });
}

export async function testMcpConnection(
  serverId: string
): Promise<{ ok: boolean; message: string }> {
  return apiFetch<{ ok: boolean; message: string }>(
    `/mcp-servers/${serverId}/test-connection`,
    { method: "POST" }
  );
}
