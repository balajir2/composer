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
  type LlmModelSummary,
} from "@/lib/api/llm-models";

const PROVIDER_OPTIONS = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "google", label: "Google" },
  { value: "groq", label: "Groq" },
];

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
        <CreateModelDialog />
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
              <TableHead className="w-48 text-right"></TableHead>
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
                <TableCell className="flex items-center justify-end gap-2">
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

function CreateModelDialog() {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState("anthropic");
  const [modelId, setModelId] = useState("");
  const [label, setLabel] = useState("");
  const qc = useQueryClient();

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
      setOpen(false);
      toast.success("Model added.");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed.");
    },
  });

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
              onValueChange={setProvider}
              options={PROVIDER_OPTIONS}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new-model-id">Model ID</Label>
            <Input
              id="new-model-id"
              required
              placeholder="e.g. claude-sonnet-4-6"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Exact identifier the provider expects (case-sensitive).
            </p>
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
            <Button
              type="submit"
              disabled={mutation.isPending || !modelId}
            >
              {mutation.isPending ? "Adding…" : "Add"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
