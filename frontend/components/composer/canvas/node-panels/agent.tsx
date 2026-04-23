"use client";

import { useQuery } from "@tanstack/react-query";

import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { getCatalog } from "@/lib/api/catalog";
import { listEnabledLlmModels } from "@/lib/api/llm-models";

const PROVIDER_OPTIONS = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "groq", label: "Groq" },
];

export default function AgentPanel({
  data,
  onChange,
}: {
  data: Record<string, unknown>;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const provider = (data.provider as string) ?? "";

  const { data: catalog = [] } = useQuery({
    queryKey: ["catalog"],
    queryFn: getCatalog,
  });

  const { data: models = [], isLoading: modelsLoading } = useQuery({
    queryKey: ["llm-models", provider],
    queryFn: () => listEnabledLlmModels(provider),
    enabled: Boolean(provider),
  });

  const selectedTools = Array.isArray(data.tools) ? (data.tools as string[]) : [];

  function toggleTool(toolId: string) {
    const next = selectedTools.includes(toolId)
      ? selectedTools.filter((t) => t !== toolId)
      : [...selectedTools, toolId];
    onChange({ tools: next });
  }

  const modelOptions = models.map((m) => ({
    value: m.modelId,
    label: m.label ?? m.modelId,
  }));

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
              value={(data.model as string) ?? ""}
              onValueChange={(v) => onChange({ model: v })}
              options={modelOptions}
              placeholder="Select model"
            />
          )
        ) : (
          <p className="text-xs text-muted-foreground">Select a provider first.</p>
        )}
      </div>

      <div className="space-y-2">
        <Label>System prompt</Label>
        <Textarea
          value={(data.systemPrompt as string) ?? ""}
          onChange={(e) => onChange({ systemPrompt: e.target.value })}
          rows={4}
          placeholder="You are a helpful assistant…"
        />
      </div>

      {catalog.length > 0 && (
        <div className="space-y-2">
          <Label>Tools</Label>
          <div className="space-y-1 rounded-md border p-2">
            {catalog.map((tool) => {
              const id = tool.id;
              const label = tool.kind === "builtin" ? tool.label : tool.name;
              return (
                <label key={id} className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selectedTools.includes(id)}
                    onChange={() => toggleTool(id)}
                    className="h-3.5 w-3.5"
                  />
                  {label}
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
