"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { adminGetBuilderModels, adminUpdateBuilderModels } from "@/lib/api/builder";
import { adminListLlmModels } from "@/lib/api/llm-models";

/**
 * Admin control for the "Build with AI" chat: which catalog models users may
 * pick, and which one is the default. The default is always allowed. Only
 * models that are enabled in the catalog can be offered, so disabling a model
 * above also removes it from the chat.
 */
export function BuilderModelsCard() {
  const qc = useQueryClient();
  const catalog = useQuery({
    queryKey: ["admin-llm-models"],
    queryFn: () => adminListLlmModels(),
  });
  const settings = useQuery({
    queryKey: ["admin-builder-models"],
    queryFn: () => adminGetBuilderModels(),
  });

  const [defaultModel, setDefaultModel] = useState("");
  const [allowed, setAllowed] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!settings.data) return;
    setDefaultModel(settings.data.defaultModel);
    setAllowed(new Set(settings.data.allowedModels));
  }, [settings.data]);

  const save = useMutation({
    mutationFn: () =>
      adminUpdateBuilderModels({
        defaultModel,
        // Default first, then the rest in catalog order.
        allowedModels: [defaultModel, ...[...allowed].filter((m) => m !== defaultModel)],
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-builder-models"] });
      toast.success("Build with AI models updated.");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Save failed."),
  });

  const options = (catalog.data ?? [])
    .filter((m) => m.enabled)
    .map((m) => ({ id: `${m.provider}/${m.modelId}`, label: m.label }));
  const defaultInCatalog = options.some((o) => o.id === defaultModel);

  function toggle(id: string) {
    setAllowed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function chooseDefault(id: string) {
    setDefaultModel(id);
    setAllowed((prev) => new Set(prev).add(id));
  }

  return (
    <section className="space-y-3 rounded-lg border p-4">
      <div>
        <h3 className="text-lg font-semibold">Build with AI models</h3>
        <p className="text-sm text-muted-foreground">
          Choose which models users can pick in the Build with AI chat. The default is always
          allowed. Only models enabled in the table below can be offered.
        </p>
      </div>

      {catalog.isLoading || settings.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : catalog.isError || settings.isError ? (
        <p className="text-sm text-destructive">Could not load the builder model settings.</p>
      ) : (
        <>
          {!defaultInCatalog && (
            <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
              The current default ({defaultModel}) is not in the model catalog. Pick one of the
              models below as the new default before saving.
            </p>
          )}
          <ul className="divide-y rounded-md border">
            {options.map((o) => (
              <li key={o.id} className="flex items-center gap-4 px-3 py-2 text-sm">
                <label className="flex flex-1 items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`Allow ${o.id}`}
                    checked={allowed.has(o.id) || o.id === defaultModel}
                    disabled={o.id === defaultModel}
                    onChange={() => toggle(o.id)}
                  />
                  <span className="font-mono text-xs">{o.id}</span>
                  {o.label && <span className="text-muted-foreground">{o.label}</span>}
                </label>
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="radio"
                    name="builder-default-model"
                    aria-label={`Default ${o.id}`}
                    checked={o.id === defaultModel}
                    onChange={() => chooseDefault(o.id)}
                  />
                  Default
                </label>
              </li>
            ))}
          </ul>
          {options.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No enabled models yet. Add one in the table below first.
            </p>
          )}
          <Button
            size="sm"
            onClick={() => save.mutate()}
            disabled={save.isPending || !defaultInCatalog}
          >
            Save
          </Button>
        </>
      )}
    </section>
  );
}
