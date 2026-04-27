"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BookOpen, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { instantiateTemplate, listWorkflows } from "@/lib/api/workflows";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";

/**
 * Template gallery — workflows flagged `isTemplate=true` that any user
 * can preview and clone into their own list as a starting point.
 *
 * Counterpart to OAB's reference workflows: same idea, different stack.
 * Designers click "Use template" → backend clones (with isTemplate=false,
 * isPublic=false) → router pushes them straight into the canvas of the
 * fresh copy where they can edit without affecting the original.
 */
export default function TemplatesPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["designer-templates"],
    queryFn: () => listWorkflows({ isTemplate: true, limit: 100 }),
  });

  const instantiate = useMutation({
    mutationFn: (templateId: string) => instantiateTemplate(templateId),
    onSuccess: (created) => {
      toast.success(`Cloned "${created.name}" — opening editor.`);
      // Invalidate the user's own list so the new copy appears next time.
      void queryClient.invalidateQueries({ queryKey: ["designer-workflows"] });
      router.push(`/designer/${created.id}`);
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "Failed to clone template."),
  });

  const items = data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Link
            href="/designer"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            My workflows
          </Link>
          <span className="text-muted-foreground">/</span>
          <h2 className="flex items-center gap-2 text-2xl font-semibold">
            <BookOpen className="h-5 w-5" />
            Templates
          </h2>
        </div>
      </div>

      <p className="max-w-2xl text-sm text-muted-foreground">
        Reference implementations to crib from. Click <strong>Use template</strong>{" "}
        and you&apos;ll get a private copy in your workflow list — the original
        stays untouched.
      </p>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-48 w-full rounded-xl" />
          ))}
        </div>
      ) : isError ? (
        <EmptyState
          title="Could not load templates"
          description="Try refreshing — the backend may be unreachable."
        />
      ) : items.length === 0 ? (
        <EmptyState
          title="No templates yet"
          description="Workflows marked as templates will show up here. Open any workflow's Settings page and toggle 'Mark as template' to publish one."
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((wf) => (
            <Card key={wf.id} className="flex flex-col transition-shadow hover:shadow-md">
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="text-base leading-snug">{wf.name}</CardTitle>
                  <Badge
                    variant="outline"
                    className="border-violet-300 bg-violet-50 text-violet-800"
                  >
                    <Sparkles className="mr-1 h-3 w-3" />
                    Template
                  </Badge>
                </div>
                <div className="flex flex-wrap gap-1">
                  {wf.category && (
                    <Badge variant="secondary" className="text-[10px]">
                      {wf.category}
                    </Badge>
                  )}
                  {wf.difficulty && (
                    <Badge variant="secondary" className="text-[10px]">
                      {wf.difficulty}
                    </Badge>
                  )}
                  {wf.estimatedTime && (
                    <Badge variant="outline" className="text-[10px]">
                      {wf.estimatedTime}
                    </Badge>
                  )}
                </div>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col justify-between gap-4">
                <p className="line-clamp-3 min-h-[3.75em] text-sm text-muted-foreground">
                  {wf.description ?? "No description provided."}
                </p>
                <div className="flex flex-col gap-2">
                  <Button
                    onClick={() => instantiate.mutate(wf.id)}
                    disabled={instantiate.isPending}
                    size="sm"
                  >
                    {instantiate.isPending && instantiate.variables === wf.id
                      ? "Cloning…"
                      : "Use template"}
                  </Button>
                  <Link
                    href={`/designer/${wf.id}`}
                    className="text-center text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  >
                    Preview the canvas (read-only)
                  </Link>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
