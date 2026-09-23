"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/composer/empty-state";
import {
  adminCreateLlmModel,
  adminDeleteLlmModel,
  adminListLlmModels,
  adminUpdateLlmModel,
  adminVerifyLlmModel,
  listAvailableModels,
  type LlmModelSummary,
} from "@/lib/api/llm-models";

const PROVIDER_OPTIONS = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "groq", label: "Groq" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "qwen", label: "Qwen" },
  { value: "typesafe", label: "TypeSafe (Jev)" },
];

// Mirrors src/api/llm_models_live.py's _DB_ONLY_PROVIDERS — purely for
// wording the empty-state message correctly. The backend already enforces
// the actual DB-only-fallback behavior regardless of this list; this copy
// only decides which sentence the admin sees when there's nothing to show.
const DB_ONLY_PROVIDERS = new Set(["typesafe"]);

export default function AdminLlmModelsPage() {
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-llm-models"],
    queryFn: () => adminListLlmModels(),
  });

  const toggleEnabled = useMutation({
    mutationFn: (m: LlmModelSummary) =>
      adminUpdateLlmModel(m.id, { enabled: !m.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-llm-models"] }),
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  const del = useMutation({
    mutationFn: (m: LlmModelSummary) => adminDeleteLlmModel(m.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-llm-models"] });
      toast.success("Model removed.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed."),
  });

  // Per-model verify probe.  We track which row is currently probing
  // (rather than a global isPending) so the admin can fire multiple
  // verifies in parallel without seeing every button stuck disabled.
  const [verifyingId, setVerifyingId] = useState<string | null>(null);
  const verify = useMutation({
    mutationFn: (m: LlmModelSummary) => {
      setVerifyingId(m.id);
      return adminVerifyLlmModel(m.id);
    },
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ["admin-llm-models"] });
      if (resp.status === "ok") {
        toast.success(`${resp.model.modelId}: verified live.`);
      } else if (resp.status === "unavailable") {
        toast.error(
          resp.auto_disabled
            ? `${resp.model.modelId} is unavailable — disabled and removed from designer dropdowns.`
            : `${resp.model.modelId}: not available for this key. ${resp.message}`
        );
      } else if (resp.status === "auth_error") {
        toast.error(
          `${resp.model.provider} key rejected — fix the key, then re-verify.`
        );
      } else {
        toast.error(`Probe failed: ${resp.message}`);
      }
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Verify failed."),
    onSettled: () => setVerifyingId(null),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">LLM models</h2>
          <p className="text-sm text-muted-foreground">
            Available models per provider. Designers pick from this list on
            agent nodes.
          </p>
        </div>
        <CreateModelDialog existing={data ?? []} />
      </div>

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : isError || !data ? (
        <EmptyState
          title="Could not load models"
          description="Try refreshing or check that the backend is up."
        />
      ) : data.length === 0 ? (
        <EmptyState
          title="No models yet"
          description="Add the first one via the Add model button."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Provider</TableHead>
              <TableHead>Model ID</TableHead>
              <TableHead>Label</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Verification</TableHead>
              <TableHead className="w-72 text-right"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((m) => (
              <TableRow key={m.id}>
                <TableCell className="font-mono text-xs">{m.provider}</TableCell>
                <TableCell className="font-mono text-xs">{m.modelId}</TableCell>
                <TableCell className="text-sm">{m.label ?? "—"}</TableCell>
                <TableCell>
                  <Badge variant={m.enabled ? "default" : "secondary"}>
                    {m.enabled ? "enabled" : "disabled"}
                  </Badge>
                </TableCell>
                <TableCell>
                  <VerificationCell model={m} />
                </TableCell>
                <TableCell className="flex items-center justify-end gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => verify.mutate(m)}
                    disabled={verifyingId === m.id}
                    title="Probe the provider with this exact model ID"
                  >
                    {verifyingId === m.id ? "Verifying…" : "Verify"}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => toggleEnabled.mutate(m)}
                    disabled={toggleEnabled.isPending}
                  >
                    {m.enabled ? "Disable" : "Enable"}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => del.mutate(m)}
                    disabled={del.isPending}
                  >
                    Delete
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diffSec = Math.round((now - then) / 1000);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86_400) return `${Math.round(diffSec / 3600)}h ago`;
  const days = Math.round(diffSec / 86_400);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function VerificationCell({ model }: { model: LlmModelSummary }) {
  if (!model.verificationStatus || !model.verifiedAt) {
    return (
      <Badge
        variant="outline"
        className="border-dashed text-xs text-muted-foreground"
        title="Click Verify to probe the provider with this model ID"
      >
        not verified
      </Badge>
    );
  }
  if (model.verificationStatus === "ok") {
    return (
      <div className="flex flex-col gap-0.5">
        <Badge
          variant="default"
          className="w-fit border-emerald-200 bg-emerald-100 text-emerald-800 hover:bg-emerald-100"
        >
          verified
        </Badge>
        <span className="text-xs text-muted-foreground">
          {relativeTime(model.verifiedAt)}
        </span>
      </div>
    );
  }
  // Unavailable / unknown status — surface the error so the admin
  // can see why and decide to disable or remove.
  return (
    <div className="flex flex-col gap-0.5">
      <Badge variant="destructive" className="w-fit" title={model.verificationMessage ?? ""}>
        unavailable
      </Badge>
      <span className="text-xs text-muted-foreground">
        {relativeTime(model.verifiedAt)}
      </span>
    </div>
  );
}

function CreateModelDialog({ existing }: { existing: LlmModelSummary[] }) {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState("anthropic");
  const [modelId, setModelId] = useState("");
  const [label, setLabel] = useState("");
  const [manualMode, setManualMode] = useState(false);
  const qc = useQueryClient();

  // Live models from the provider's /models API.  We deliberately avoid
  // frontend caching (staleTime: 0 + refetchOnMount: "always") so every
  // time an admin opens the dialog the list is re-fetched — no stale
  // entries surviving provider model releases/retirements.  The backend
  // still caches for 5 minutes to protect the upstream API; the Refresh
  // button below forces a bypass via ?refresh=1 when needed.
  const [forceRefreshCounter, setForceRefreshCounter] = useState(0);
  const {
    data: liveResp,
    isLoading: liveLoading,
    isFetching: liveFetching,
    isError: liveError,
    refetch,
  } = useQuery({
    queryKey: ["admin-available-models", provider, open, forceRefreshCounter],
    queryFn: () => listAvailableModels(provider, forceRefreshCounter > 0),
    enabled: open && Boolean(provider) && !manualMode,
    staleTime: 0,
    refetchOnMount: "always",
    gcTime: 0,
  });

  const alreadyAddedIds = new Set(
    existing.filter((m) => m.provider === provider).map((m) => m.modelId)
  );
  const liveModels = (liveResp?.models ?? []).filter(
    (m) => !alreadyAddedIds.has(m.modelId)
  );
  const fellBackToDb =
    !liveLoading &&
    !liveError &&
    liveModels.length > 0 &&
    liveModels.every((m) => m.source === "db");

  const modelOptions = liveModels.map((m) => ({
    value: m.modelId,
    label: m.label ? `${m.label} — ${m.modelId}` : m.modelId,
  }));

  const mutation = useMutation({
    mutationFn: () =>
      adminCreateLlmModel({
        provider,
        modelId,
        label: label || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-llm-models"] });
      setModelId("");
      setLabel("");
      setManualMode(false);
      setOpen(false);
      toast.success("Model added.");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed.");
    },
  });

  function handleProviderChange(next: string) {
    setProvider(next);
    setModelId("");
    setLabel("");
  }

  function handleModelPick(next: string) {
    setModelId(next);
    // Auto-fill Label with the provider-supplied label when available.
    const picked = liveModels.find((m) => m.modelId === next);
    if (picked?.label && !label) setLabel(picked.label);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button onClick={() => setOpen(true)}>Add model</Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add an LLM model</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
          className="space-y-4"
        >
          <div className="space-y-2">
            <Label htmlFor="new-model-provider">Provider</Label>
            <NativeSelect
              id="new-model-provider"
              value={provider}
              onValueChange={handleProviderChange}
              options={PROVIDER_OPTIONS}
            />
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="new-model-id">Model ID</Label>
              <div className="flex items-center gap-3">
                {!manualMode && (
                  <button
                    type="button"
                    onClick={() => {
                      setForceRefreshCounter((n) => n + 1);
                      void refetch();
                    }}
                    disabled={liveFetching}
                    className="text-xs text-muted-foreground underline hover:text-foreground disabled:opacity-50"
                    title="Bypass the 5-minute server cache and re-hit the provider"
                  >
                    {liveFetching ? "Refreshing…" : "Refresh"}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setManualMode((v) => !v);
                    setModelId("");
                  }}
                  className="text-xs text-muted-foreground underline hover:text-foreground"
                >
                  {manualMode ? "← Pick from list" : "Enter manually instead"}
                </button>
              </div>
            </div>
            {manualMode ? (
              <>
                <Input
                  id="new-model-id"
                  required
                  placeholder="e.g. claude-sonnet-4-5-20250929"
                  value={modelId}
                  onChange={(e) => setModelId(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Exact identifier the provider expects (case-sensitive).
                </p>
              </>
            ) : liveLoading ? (
              <p className="text-xs text-muted-foreground">
                Fetching available models from {provider}…
              </p>
            ) : liveError ? (
              <p className="text-xs text-destructive">
                Couldn&apos;t reach {provider}. Check the{" "}
                <a href="/admin/llm-keys" className="underline">
                  {provider} API key
                </a>{" "}
                or switch to manual entry.
              </p>
            ) : modelOptions.length === 0 && DB_ONLY_PROVIDERS.has(provider) ? (
              <p className="text-xs text-muted-foreground">
                {provider} has no live model catalog to discover from — models
                are admin-curated only. Switch to manual entry to add your
                first one.
              </p>
            ) : modelOptions.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No new {provider} models to add — everything the provider
                exposes is already in the table. Switch to manual entry if
                you need a model the provider hasn&apos;t listed yet.
              </p>
            ) : (
              <>
                <NativeSelect
                  id="new-model-id"
                  value={modelId}
                  onValueChange={handleModelPick}
                  options={modelOptions}
                  placeholder={`Pick from ${modelOptions.length} live ${provider} model${modelOptions.length === 1 ? "" : "s"}`}
                />
                <p className="text-xs text-muted-foreground">
                  {fellBackToDb
                    ? `Couldn't reach ${provider} just now — showing last-known list from the DB.`
                    : `Fetched from ${provider} /models API (already-added models are hidden).`}
                </p>
              </>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-model-label">Label (optional)</Label>
            <Input
              id="new-model-label"
              placeholder="e.g. Claude Sonnet 4.6"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending || !modelId}>
              {mutation.isPending ? "Adding…" : "Add"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
