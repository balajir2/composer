"use client";

import { useQuery } from "@tanstack/react-query";
import type { Node as RFNode } from "reactflow";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { getCatalog, type CatalogTool } from "@/lib/api/catalog";
import { listEnabledLlmModels } from "@/lib/api/llm-models";
import { PromptField } from "../prompt-field";

const PROVIDER_OPTIONS = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "groq", label: "Groq" },
];

const OUTPUT_FORMAT_OPTIONS = [
  // Stored value stays "text" for backend compatibility (agent.py checks
  // `output_format == "json"` and falls through to text otherwise); the
  // label is what designers see.
  { value: "text", label: "Verbose" },
  { value: "json", label: "JSON" },
];

const JSON_SCHEMA_PLACEHOLDER = `{
  "type": "object",
  "properties": {
    "place": { "type": "string", "description": "Canonical place name" },
    "country": { "type": "string" }
  },
  "required": ["place"]
}`;

// Built-in providers expose exactly one LangChain tool each; the backend's
// tool registry resolves a qualified "provider.tool" string via
// src.tools.registry._split_qualified().  Keep this map in lock-step with
// the providers under src/tools/providers/*.
const BUILTIN_QUALIFIED: Record<string, string> = {
  tavily: "tavily.tavily_search",
  serper: "serper.serper_search",
  firecrawl: "firecrawl.firecrawl_scrape",
  browserless: "browserless.browserless_fetch",
};

function qualifiedFor(tool: CatalogTool): string | null {
  if (tool.kind === "builtin") return BUILTIN_QUALIFIED[tool.id] ?? null;
  return null; // MCP tools are selected via mcpServerIds, not selectedTools
}

export default function AgentPanel({
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
  const provider = (data.provider as string) ?? "";

  const { data: catalog = [] } = useQuery({
    queryKey: ["catalog"],
    queryFn: getCatalog,
  });

  // Designers pick from the admin-curated list.  Admins keep that list
  // fresh via Admin → LLM models, where the Add-model dialog offers a
  // dropdown populated from the provider's live /models endpoint.
  const { data: models = [], isLoading: modelsLoading } = useQuery({
    queryKey: ["llm-models", provider],
    queryFn: () => listEnabledLlmModels(provider),
    enabled: Boolean(provider),
  });

  const selectedTools: string[] = Array.isArray(data.selectedTools)
    ? (data.selectedTools as string[])
    : [];
  const mcpServerIds: string[] = Array.isArray(data.mcpServerIds)
    ? (data.mcpServerIds as string[])
    : [];

  function isBuiltinSelected(qualified: string) {
    return selectedTools.includes(qualified);
  }

  function toggleBuiltin(qualified: string) {
    const next = selectedTools.includes(qualified)
      ? selectedTools.filter((t) => t !== qualified)
      : [...selectedTools, qualified];
    // `data.tools` is a backend-internal field (list of AgentTool objects).
    // If an old build accidentally stored selections there, wipe it so save
    // no longer 422s on the wrong shape.
    onChange({ selectedTools: next, tools: undefined });
  }

  function toggleMcp(serverId: string) {
    const next = mcpServerIds.includes(serverId)
      ? mcpServerIds.filter((t) => t !== serverId)
      : [...mcpServerIds, serverId];
    onChange({ mcpServerIds: next, tools: undefined });
  }

  const modelOptions = models.map((m) => ({
    // Backend expects "<provider>/<modelId>" so LangChain can dispatch to the
    // right chat-model constructor (src/llm/providers.py).  Saving bare
    // modelId caused the backend to default to OpenAI and 404 for Claude
    // names like "claude-haiku-4-5".
    value: `${provider}/${m.modelId}`,
    label: m.label ? `${m.label} (${m.modelId})` : m.modelId,
  }));

  // Back-compat: legacy saves stored a bare modelId (or an anthropic/… string
  // with a different provider).  Reconcile the stored value against the new
  // option format so the dropdown highlights the correct entry.
  const storedModel = (data.model as string) ?? "";
  const normalizedModel = storedModel.includes("/")
    ? storedModel
    : storedModel
      ? `${provider}/${storedModel}`
      : "";

  // The backend reads `data.instructions` only.  Fall back to the legacy
  // `systemPrompt` / `prompt` keys so workflows saved by older builds still
  // surface their content in the single field; the next Save rewrites them
  // onto `instructions` and clears the legacy keys.
  const instructions =
    (data.instructions as string) ??
    (data.systemPrompt as string) ??
    (data.prompt as string) ??
    "";

  function handleInstructionsChange(next: string) {
    onChange({ instructions: next, systemPrompt: undefined, prompt: undefined });
  }

  // Output shape: "text" (default) or "json" (structured output).  When
  // JSON is selected the agent's final response is validated against the
  // provided schema and downstream nodes can reference individual fields
  // via `{{<nodeId>.<field>}}` (the variable picker expands schema
  // properties automatically once saved).
  const outputFormat = ((data.outputFormat as string) ?? "text").toLowerCase();
  const jsonSchemaStored = data.jsonSchema;
  const jsonSchemaText =
    typeof jsonSchemaStored === "string"
      ? jsonSchemaStored
      : jsonSchemaStored && typeof jsonSchemaStored === "object"
        ? JSON.stringify(jsonSchemaStored, null, 2)
        : "";
  const jsonSchemaValid = (() => {
    if (outputFormat !== "json") return true;
    if (!jsonSchemaText.trim()) return true;
    try {
      JSON.parse(jsonSchemaText);
      return true;
    } catch {
      return false;
    }
  })();

  function handleOutputFormatChange(next: string) {
    // Clear schema when switching back to text so the stored shape matches
    // the rendered UI.
    if (next === "text") {
      onChange({ outputFormat: "text", jsonSchema: undefined });
    } else {
      onChange({ outputFormat: next });
    }
  }

  function handleJsonSchemaChange(raw: string) {
    // Save as object when it parses (so the backend + variable picker can
    // walk properties without re-parsing a string), string otherwise so the
    // user's in-progress edit isn't destroyed.
    if (!raw.trim()) {
      onChange({ jsonSchema: undefined });
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      onChange({ jsonSchema: parsed });
    } catch {
      onChange({ jsonSchema: raw });
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="agent-provider">Provider</Label>
        <NativeSelect
          id="agent-provider"
          value={provider}
          onValueChange={(v) => onChange({ provider: v, model: "" })}
          options={PROVIDER_OPTIONS}
          placeholder="Select provider"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="agent-model">Model</Label>
        {provider ? (
          modelsLoading ? (
            <p className="text-xs text-muted-foreground">Loading models…</p>
          ) : modelOptions.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No models configured for this provider. Ask an admin to add one under
              Admin → LLM models.
            </p>
          ) : (
            <NativeSelect
              id="agent-model"
              value={normalizedModel}
              onValueChange={(v) => onChange({ model: v })}
              options={modelOptions}
              placeholder="Select model"
            />
          )
        ) : (
          <p className="text-xs text-muted-foreground">Select a provider first.</p>
        )}
      </div>

      <PromptField
        label="Prompt"
        value={instructions}
        onChange={handleInstructionsChange}
        nodes={allNodes ?? []}
        currentNodeId={currentNodeId ?? ""}
        rows={8}
        placeholder={
          "Describe what this agent should do. Reference upstream variables with {{name}} — e.g.,\n\nSummarize the following support ticket for {{customer_name}}:\n{{lastOutput}}"
        }
      />

      <div className="space-y-2">
        <Label htmlFor="agent-output-format">Output format</Label>
        <NativeSelect
          id="agent-output-format"
          value={outputFormat}
          onValueChange={handleOutputFormatChange}
          options={OUTPUT_FORMAT_OPTIONS}
        />
        <p className="text-xs text-muted-foreground">
          <strong>Verbose</strong> returns free-form prose — good for
          summaries and answers. Pick <strong>JSON</strong> when downstream
          nodes need to reference individual fields like{" "}
          <code className="font-mono">&#123;&#123;nodeId.place&#125;&#125;</code>.
          The schema below both validates the agent&apos;s output and tells
          the variable picker what fields exist.
        </p>
      </div>

      {outputFormat === "json" && (
        <div className="space-y-2">
          <Label htmlFor="agent-json-schema">JSON schema</Label>
          <Textarea
            id="agent-json-schema"
            value={jsonSchemaText}
            onChange={(e) => handleJsonSchemaChange(e.target.value)}
            rows={8}
            placeholder={JSON_SCHEMA_PLACEHOLDER}
            className="font-mono text-xs"
          />
          {!jsonSchemaValid && (
            <p className="text-xs text-destructive">
              Not valid JSON — downstream field references won&apos;t resolve
              until this parses.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Standard JSON-schema. Leave blank for free-form JSON (no
            per-field references in the picker).
          </p>
        </div>
      )}

      {catalog.length > 0 && (
        <div className="space-y-2">
          <Label>Tools</Label>
          <div className="space-y-1 rounded-md border p-2">
            {catalog.map((tool) => {
              const qualified = qualifiedFor(tool);
              const checked =
                tool.kind === "builtin"
                  ? qualified !== null && isBuiltinSelected(qualified)
                  : mcpServerIds.includes(tool.id);
              const label = tool.kind === "builtin" ? tool.label : tool.name;
              return (
                <label key={tool.id} className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      if (tool.kind === "builtin" && qualified) {
                        toggleBuiltin(qualified);
                      } else if (tool.kind === "mcp") {
                        toggleMcp(tool.id);
                      }
                    }}
                    disabled={tool.kind === "builtin" && qualified === null}
                    className="h-3.5 w-3.5"
                  />
                  {label}
                </label>
              );
            })}
          </div>
        </div>
      )}

      <div className="space-y-2">
        <Label htmlFor="agent-max-iterations">Max iterations</Label>
        <Input
          id="agent-max-iterations"
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
          How many LLM ↔ tool round-trips before giving up. Default 10,
          hard ceiling 100. Bump for long research flows.
        </p>
      </div>
    </div>
  );
}
