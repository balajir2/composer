"use client";

import { useQuery } from "@tanstack/react-query";
import type { Node as RFNode } from "reactflow";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { getCatalog } from "@/lib/api/catalog";
import { listEnabledLlmModels } from "@/lib/api/llm-models";
import { listMcpServers } from "@/lib/api/mcp-servers";
import { PromptField } from "../prompt-field";

const PROVIDER_OPTIONS = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "groq", label: "Groq" },
];

type ServerTool = { name?: unknown; description?: unknown };

export default function McpPanel({
  data,
  onChange,
  allNodes,
  currentNodeId,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
  allNodes?: RFNode[];
  currentNodeId?: string;
}) {
  const { data: catalog = [] } = useQuery({
    queryKey: ["catalog"],
    queryFn: getCatalog,
  });
  const { data: servers = [] } = useQuery({
    queryKey: ["admin-mcp-servers"],
    queryFn: () => listMcpServers(),
  });

  const sharedMcps = catalog.filter((t) => t.kind === "mcp") as {
    kind: "mcp";
    id: string;
    name: string;
    url: string;
  }[];
  const serverOptions = sharedMcps.map((m) => ({
    value: m.id,
    label: m.name,
  }));

  const serverId = (data.mcpServerId as string) ?? "";
  // Selected tools live under `selectedToolNames` in the new agent-mode;
  // fall back to the legacy single `toolName` if present so migrating
  // workflows keep their config on first Save.
  const selectedRaw = data.selectedToolNames;
  const legacyToolName = (data.toolName as string) ?? "";
  const selectedToolNames: string[] = Array.isArray(selectedRaw)
    ? (selectedRaw as string[])
    : legacyToolName
      ? [legacyToolName]
      : [];

  // The server row caches its tools/list after a successful Test-connection;
  // we read them straight from that cache so the panel can offer a concrete
  // multi-select without round-tripping through the provider again.
  const selectedServer = servers.find((s) => s.id === serverId);
  const serverTools: ServerTool[] = Array.isArray(selectedServer?.tools)
    ? (selectedServer?.tools as ServerTool[])
    : [];

  function toggleTool(name: string) {
    const next = selectedToolNames.includes(name)
      ? selectedToolNames.filter((t) => t !== name)
      : [...selectedToolNames, name];
    onChange({ selectedToolNames: next, toolName: undefined });
  }

  // Model picker (optional — blank uses the backend's DEFAULT_MODEL).
  const provider = (data.provider as string) ?? "";
  const storedModel = (data.model as string) ?? "";
  const normalizedModel = storedModel.includes("/")
    ? storedModel
    : storedModel && provider
      ? `${provider}/${storedModel}`
      : "";

  // Designers pick from the admin-curated enabled list — same source as the
  // Agent panel.  The live-provider list lives on the Admin → LLM models
  // Add-model dialog so admins decide which models designers may use.
  const { data: models = [], isLoading: modelsLoading } = useQuery({
    queryKey: ["llm-models", provider],
    queryFn: () => listEnabledLlmModels(provider),
    enabled: Boolean(provider),
  });
  const modelOptions = models.map((m) => ({
    value: `${provider}/${m.modelId}`,
    label: m.label ? `${m.label} — ${m.modelId}` : m.modelId,
  }));

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="mcp-server">MCP Server</Label>
        {serverOptions.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No shared MCP servers yet. Ask an admin to add one under Admin → MCP
            servers and mark it as <strong>shared</strong>.
          </p>
        ) : (
          <NativeSelect
            id="mcp-server"
            value={serverId}
            onValueChange={(v) =>
              // Clear tool selections when the server changes — they're
              // keyed to the old server's namespace.
              onChange({
                mcpServerId: v,
                selectedToolNames: [],
                toolName: undefined,
              })
            }
            options={serverOptions}
            placeholder="Select server"
          />
        )}
      </div>

      {serverId && (
        <div className="space-y-2">
          <Label>Tools</Label>
          {serverTools.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              This server hasn&apos;t exposed a tool list yet. Go to{" "}
              <strong>Admin → MCP servers</strong> and click <strong>Test</strong>{" "}
              on this server — that populates its tool cache.
            </p>
          ) : (
            <div className="space-y-1 rounded-md border p-2">
              {serverTools.map((t, i) => {
                const name = typeof t.name === "string" ? t.name : `tool_${i}`;
                const desc = typeof t.description === "string" ? t.description : "";
                return (
                  <label
                    key={name}
                    className="flex cursor-pointer items-start gap-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={selectedToolNames.includes(name)}
                      onChange={() => toggleTool(name)}
                      className="mt-0.5 h-3.5 w-3.5"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-mono text-xs">{name}</div>
                      {desc && (
                        <div className="truncate text-xs text-muted-foreground">
                          {desc}
                        </div>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            Leave all unchecked to let the model pick from every tool this server
            offers.
          </p>
        </div>
      )}

      <PromptField
        label="Prompt"
        value={(data.instructions as string) ?? ""}
        onChange={(next) => onChange({ instructions: next })}
        nodes={allNodes ?? []}
        currentNodeId={currentNodeId ?? ""}
        rows={6}
        placeholder={
          "Describe what this node should do with the selected MCP tools. Reference upstream variables with {{name}} — e.g.,\n\nSearch Highspot for the most recent case studies matching {{topic}} and return the top 3 as bullets."
        }
      />

      <div className="space-y-2">
        <Label htmlFor="mcp-provider">Model provider (optional)</Label>
        <NativeSelect
          id="mcp-provider"
          value={provider}
          onValueChange={(v) => onChange({ provider: v, model: "" })}
          options={[{ value: "", label: "Use default" }, ...PROVIDER_OPTIONS]}
        />
      </div>

      {provider && (
        <div className="space-y-2">
          <Label htmlFor="mcp-model">Model</Label>
          {modelsLoading ? (
            <p className="text-xs text-muted-foreground">Loading models…</p>
          ) : modelOptions.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No models enabled for this provider. Ask an admin to add one
              under Admin → LLM models.
            </p>
          ) : (
            <NativeSelect
              id="mcp-model"
              value={normalizedModel}
              onValueChange={(v) => onChange({ model: v })}
              options={modelOptions}
              placeholder="Select model"
            />
          )}
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="mcp-max-iterations">Max iterations</Label>
        <Input
          id="mcp-max-iterations"
          type="number"
          min={1}
          max={100}
          placeholder="10 (default)"
          value={
            typeof data.maxIterations === "number"
              ? String(data.maxIterations)
              : ""
          }
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") {
              onChange({ maxIterations: undefined });
              return;
            }
            const n = Math.min(Math.max(parseInt(v, 10) || 0, 1), 100);
            onChange({ maxIterations: n });
          }}
        />
        <p className="text-xs text-muted-foreground">
          How many LLM ↔ tool round-trips the node is allowed before giving
          up. Default 10, hard ceiling 100. Bump to 20–30 for long research
          flows (many search + scrape calls).
        </p>
      </div>

      {!data.instructions && (data.toolName as string | undefined) && (
        <div className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">
          <p className="font-medium">Legacy deterministic mode</p>
          <p className="mt-0.5">
            This node is configured for a single tool call (
            <code className="font-mono">{data.toolName as string}</code>). Write a
            Prompt above to switch it into agent mode.
          </p>
          <Input
            className="mt-2"
            value={(data.toolName as string) ?? ""}
            onChange={(e) => onChange({ toolName: e.target.value })}
          />
        </div>
      )}
    </div>
  );
}
