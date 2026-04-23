import { apiFetch } from "./client";
import type { components } from "./generated/schema";

type McpServerRead = components["schemas"]["McpServerRead"];

export type CatalogTool =
  | { kind: "builtin"; id: "tavily" | "serper" | "firecrawl" | "browserless"; label: string }
  | { kind: "mcp"; id: string; name: string; url: string };

export async function getCatalog(): Promise<CatalogTool[]> {
  // Shared MCPs: GET /mcp-servers (filter applied client-side)
  const mcps = await apiFetch<McpServerRead[]>(`/mcp-servers`);
  const sharedMcps = mcps.filter((m) => m.isShared === true);

  const settings = await apiFetch<{ key: string; value: string }[]>(
    `/admin/deployment-settings`
  ).catch(() => [] as { key: string; value: string }[]);

  const isToolEnabled = (id: string) => {
    const row = settings.find((s) => s.key === `tool.${id}.enabled`);
    return row?.value !== "false"; // default enabled
  };

  const builtins: CatalogTool[] = (
    [
      { kind: "builtin" as const, id: "tavily" as const, label: "Tavily search" },
      { kind: "builtin" as const, id: "serper" as const, label: "Serper search" },
      { kind: "builtin" as const, id: "firecrawl" as const, label: "Firecrawl scrape" },
      { kind: "builtin" as const, id: "browserless" as const, label: "Browserless" },
    ] as CatalogTool[]
  ).filter((t) => isToolEnabled((t as { id: string }).id));

  return [
    ...builtins,
    ...sharedMcps.map((m): CatalogTool => ({ kind: "mcp", id: m.id, name: m.name, url: m.url })),
  ];
}
