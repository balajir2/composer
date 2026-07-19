"use client";

import { useQuery } from "@tanstack/react-query";
import type { Node as RFNode } from "reactflow";

import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { listEnabledLlmModels } from "@/lib/api/llm-models";
import { PromptField } from "../prompt-field";

// Must match JIRA_TOKEN_REDACTED in src/engine/workflow.py — the backend
// never returns the real token once it's saved, only this marker.
const JIRA_TOKEN_REDACTED_MARKER = "••••••••";

const PROVIDER_OPTIONS = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "groq", label: "Groq" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "qwen", label: "Qwen" },
];

const OPERATION_OPTIONS = [
  { value: "agent", label: "Agent — LLM decides which Jira action to take" },
  { value: "extract", label: "Extract — deterministic bulk JQL search, no LLM" },
];

export default function JiraPanel({
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
  const rawApiToken = (data.apiToken as string) ?? "";
  const apiTokenIsRedacted = rawApiToken === JIRA_TOKEN_REDACTED_MARKER;

  const operation = (data.operation as string) || "agent";
  const jql = (data.jql as string) ?? "";
  const fieldsList = (data.fields as string[] | undefined) ?? [];
  const expandChangelog = (data.expandChangelog as boolean | undefined) ?? true;
  const maxIssues = (data.maxIssues as number | undefined) ?? 1000;

  const provider = (data.provider as string) ?? "";
  const storedModel = (data.model as string) ?? "";
  const normalizedModel = storedModel.includes("/")
    ? storedModel
    : storedModel && provider
      ? `${provider}/${storedModel}`
      : "";

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
      {/* Credentials */}
      <div className="rounded-md border bg-muted/20 p-3">
        <p className="mb-2 text-xs font-semibold text-muted-foreground">
          Jira Connection
        </p>
        <div className="space-y-2">
          <div className="space-y-1">
            <Label htmlFor="jira-domain" className="text-xs">
              Domain
            </Label>
            <Input
              id="jira-domain"
              value={(data.domain as string) ?? ""}
              onChange={(e) => onChange({ domain: e.target.value })}
              placeholder="your-org.atlassian.net"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="jira-email" className="text-xs">
              Email
            </Label>
            <Input
              id="jira-email"
              value={(data.email as string) ?? ""}
              onChange={(e) => onChange({ email: e.target.value })}
              placeholder="you@example.com"
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="jira-api-token" className="text-xs">
              API Token
            </Label>
            <Input
              id="jira-api-token"
              type="password"
              value={apiTokenIsRedacted ? "" : rawApiToken}
              onChange={(e) => onChange({ apiToken: e.target.value })}
              placeholder={
                apiTokenIsRedacted
                  ? "API token is set — leave blank to keep, type to replace"
                  : "••••••••"
              }
              className="font-mono text-xs"
            />
            <p className="text-[10px] text-muted-foreground">
              Generate at{" "}
              <a
                href="https://id.atlassian.com/manage/api-tokens"
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                id.atlassian.com/manage/api-tokens
              </a>
            </p>
          </div>
        </div>
      </div>

      {/* Operation */}
      <div className="space-y-2">
        <Label htmlFor="jira-operation">Operation</Label>
        <NativeSelect
          id="jira-operation"
          value={operation}
          onValueChange={(v) => onChange({ operation: v })}
          options={OPERATION_OPTIONS}
        />
      </div>

      {operation === "agent" && (
        <>
          {/* Instructions */}
          <PromptField
            label="Prompt"
            value={(data.instructions as string) ?? ""}
            onChange={(next) => onChange({ instructions: next })}
            nodes={allNodes ?? []}
            currentNodeId={currentNodeId ?? ""}
            rows={6}
            placeholder={
              "Describe what this node should do with Jira. Reference upstream variables with {{name}}.\n\nExample: Search for all open bugs in project PROJ and create a summary of the top 3 by priority."
            }
          />

          {/* Model picker */}
          <div className="space-y-2">
            <Label htmlFor="jira-provider">Model provider (optional)</Label>
            <NativeSelect
              id="jira-provider"
              value={provider}
              onValueChange={(v) => onChange({ provider: v, model: "" })}
              options={[{ value: "", label: "Use default" }, ...PROVIDER_OPTIONS]}
            />
          </div>

          {provider && (
            <div className="space-y-2">
              <Label htmlFor="jira-model">Model</Label>
              {modelsLoading ? (
                <p className="text-xs text-muted-foreground">Loading models…</p>
              ) : modelOptions.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No models enabled for this provider. Ask an admin to add one
                  under Admin → LLM models.
                </p>
              ) : (
                <NativeSelect
                  id="jira-model"
                  value={normalizedModel}
                  onValueChange={(v) => onChange({ model: v })}
                  options={modelOptions}
                  placeholder="Select model"
                />
              )}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="jira-max-iterations">Max iterations</Label>
            <Input
              id="jira-max-iterations"
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
              up. Default 10, hard ceiling 100.
            </p>
          </div>
        </>
      )}

      {operation === "extract" && (
        <>
          <div className="space-y-2">
            <Label htmlFor="jira-jql">JQL</Label>
            <Textarea
              id="jira-jql"
              value={jql}
              onChange={(e) => onChange({ jql: e.target.value })}
              placeholder="project = MB AND updated >= -7d"
              rows={2}
              className="font-mono text-xs"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="jira-fields">Fields</Label>
            <Input
              id="jira-fields"
              value={fieldsList.join(", ")}
              onChange={(e) =>
                onChange({
                  fields: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
              placeholder="summary, status, assignee, duedate"
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground">
              Comma-separated Jira field names/IDs to request. No default —
              every workflow specifies exactly what it needs.
            </p>
          </div>

          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <div>
              <p className="text-sm font-medium">Expand changelog</p>
              <p className="text-xs text-muted-foreground">
                Include status-transition history per issue (needed for
                blocker-age/staleness calculations downstream).
              </p>
            </div>
            <input
              id="jira-expand-changelog"
              aria-label="Expand changelog"
              type="checkbox"
              checked={expandChangelog}
              onChange={(e) => onChange({ expandChangelog: e.target.checked })}
              className="h-4 w-4"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="jira-max-issues">Max issues</Label>
            <Input
              id="jira-max-issues"
              type="number"
              min={1}
              max={5000}
              value={maxIssues}
              onChange={(e) =>
                onChange({
                  maxIssues: Math.min(Math.max(parseInt(e.target.value, 10) || 1, 1), 5000),
                })
              }
            />
            <p className="text-xs text-muted-foreground">
              Hard cap on issues fetched. Default 1000, ceiling 5000.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
