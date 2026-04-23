"use client";

import { useQuery } from "@tanstack/react-query";
import { listWorkflows } from "@/lib/api/workflows";
import { WorkflowCard } from "@/components/composer/workflow-card";
import { EmptyState } from "@/components/composer/empty-state";
import { Skeleton } from "@/components/ui/skeleton";

export default function RunsHome() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["runnable-workflows"],
    // The backend filter returns public + owned.  We further narrow to
    // production-only here since those are the only ones with a stable
    // external contract.  Non-production (drafts) can still be run via
    // designer's run-as-draft but don't show up in the end-user list.
    queryFn: () => listWorkflows({ limit: 100 }),
  });

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-40 w-full" />
        ))}
      </div>
    );
  }

  if (isError || !data) {
    return (
      <EmptyState
        title="Could not load workflows"
        description="Try refreshing the page. If this keeps happening, check your network."
      />
    );
  }

  const runnable = data.items.filter((wf) => wf.isProduction);

  if (runnable.length === 0) {
    return (
      <EmptyState
        title="No workflows to run yet"
        description="Published workflows will appear here. Ask an admin or a designer to publish one."
      />
    );
  }

  return (
    <div>
      <h2 className="pb-6 text-2xl font-semibold">Run a workflow</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {runnable.map((wf) => (
          <WorkflowCard key={wf.id} wf={wf} href={`/runs/${wf.id}`} />
        ))}
      </div>
    </div>
  );
}
