"use client";

import { useQuery } from "@tanstack/react-query";
import { getWorkflow } from "@/lib/api/workflows";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { WorkflowInputForm } from "@/components/composer/workflow-input-form";

export default function WorkflowDetails({ params }: { params: { workflowId: string } }) {
  const { workflowId } = params;
  const { data, isLoading, isError } = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => getWorkflow(workflowId),
  });

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (isError || !data) {
    return (
      <EmptyState
        title="Workflow not found"
        description="You don't have access to this workflow, or it doesn't exist."
      />
    );
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">{data.name}</h2>
        {data.description && (
          <p className="text-muted-foreground pt-1 text-sm">{data.description}</p>
        )}
      </div>
      <WorkflowInputForm workflow={data} />
    </div>
  );
}
