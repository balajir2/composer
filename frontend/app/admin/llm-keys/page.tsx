"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listLlmKeys, upsertLlmKey, deleteLlmKey } from "@/lib/api/admin";
import type { components } from "@/lib/api/generated/schema";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { LlmKeyTestButton } from "@/components/composer/llm-key-test-button";
import { toast } from "sonner";

type LlmKeySummary = components["schemas"]["LlmKeySummary"];

const PROVIDERS = [
  { id: "anthropic", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "google", label: "Google" },
  { id: "groq", label: "Groq" },
  { id: "langsmith", label: "LangSmith" },
  { id: "tavily", label: "Tavily" },
  { id: "firecrawl", label: "Firecrawl" },
  { id: "serper", label: "Serper" },
  { id: "browserless", label: "Browserless" },
  { id: "gamma", label: "Gamma" },
  { id: "resend", label: "Resend" },
] as const;

function SetKeyDialog({
  provider,
  providerLabel,
  hasExistingKey,
  onClose,
}: {
  provider: string;
  providerLabel: string;
  hasExistingKey: boolean;
  onClose: () => void;
}) {
  const [value, setValue] = useState("");
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: (v: string) => upsertLlmKey(provider, { value: v }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["llm-keys"] });
      toast.success(`${providerLabel} key ${hasExistingKey ? "updated" : "saved"}.`);
      onClose();
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed to save key."),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>
          {hasExistingKey ? `Update ${providerLabel} key` : `Set ${providerLabel} key`}
        </DialogTitle>
        <DialogDescription>
          Keys live in Postgres, encrypted with AES-256-GCM. The existing value
          can never be revealed — only replaced. Paste the new key below to
          overwrite.
        </DialogDescription>
      </DialogHeader>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate(value);
        }}
        className="space-y-4"
      >
        <div className="space-y-2">
          <Label htmlFor={`key-${provider}`}>API key</Label>
          <Input
            id={`key-${provider}`}
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Paste key here"
            required
            autoComplete="off"
          />
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={mutation.isPending || value.trim() === ""}>
            {mutation.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}

function LlmKeyRow({
  provider,
  providerLabel,
  summary,
}: {
  provider: string;
  providerLabel: string;
  summary: LlmKeySummary | undefined;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const qc = useQueryClient();

  const deleteMutation = useMutation({
    mutationFn: () => deleteLlmKey(provider),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["llm-keys"] });
      toast.success(`${providerLabel} key removed.`);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Failed to delete key."),
  });

  return (
    <>
      <TableRow>
        <TableCell className="font-medium">{providerLabel}</TableCell>
        <TableCell>
          {summary ? (
            <code className="text-xs text-muted-foreground">{summary.key_prefix}…</code>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="text-xs text-muted-foreground">
          {summary
            ? new Date(summary.updated_at).toLocaleDateString(undefined, {
                year: "numeric",
                month: "short",
                day: "numeric",
              })
            : "—"}
        </TableCell>
        <TableCell className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setDialogOpen(true)}>
            {summary ? "Update" : "Set"}
          </Button>
          {summary && (
            <>
              <LlmKeyTestButton provider={provider} providerLabel={providerLabel} />
              <Button
                size="sm"
                variant="outline"
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? "Removing…" : "Delete"}
              </Button>
            </>
          )}
        </TableCell>
      </TableRow>
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        {dialogOpen && (
          <SetKeyDialog
            provider={provider}
            providerLabel={providerLabel}
            hasExistingKey={Boolean(summary)}
            onClose={() => setDialogOpen(false)}
          />
        )}
      </Dialog>
    </>
  );
}

export default function AdminLlmKeysPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["llm-keys"],
    queryFn: () => listLlmKeys(),
  });

  function getSummary(providerId: string): LlmKeySummary | undefined {
    return data?.find((k) => k.provider === providerId);
  }

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">LLM keys</h2>
      <p className="text-sm text-muted-foreground">
        Manage API keys for LLM providers and integrations. Keys are stored encrypted.
      </p>
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : isError ? (
        <EmptyState
          title="Could not load LLM keys"
          description="Try refreshing the page. If this keeps happening, check your network."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Provider</TableHead>
              <TableHead>Key prefix</TableHead>
              <TableHead>Last updated</TableHead>
              <TableHead className="w-48">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {PROVIDERS.map((p) => (
              <LlmKeyRow
                key={p.id}
                provider={p.id}
                providerLabel={p.label}
                summary={getSummary(p.id)}
              />
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
