"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { listWorkflows } from "@/lib/api/workflows";
import { DesignerWorkflowCard } from "@/components/composer/designer-workflow-card";
import { NewWorkflowDialog } from "@/components/composer/new-workflow-dialog";
import { EmptyState } from "@/components/composer/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Button, buttonVariants } from "@/components/ui/button";
import { BookOpen, Plus } from "lucide-react";
import { cn } from "@/lib/utils";

export default function DesignerHome() {
  const [dialogOpen, setDialogOpen] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["designer-workflows"],
    queryFn: () => listWorkflows({ mine: true, limit: 100 }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-2xl font-semibold">My workflows</h2>
        <div className="flex items-center gap-2">
          <Link
            href="/designer/templates"
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            <BookOpen className="mr-1" />
            Templates
          </Link>
          <Button onClick={() => setDialogOpen(true)}>
            <Plus className="mr-1" />
            New workflow
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-48 w-full rounded-xl" />
          ))}
        </div>
      ) : isError || !data ? (
        <EmptyState
          title="Could not load workflows"
          description="Try refreshing the page. If this keeps happening, check your network."
        />
      ) : data.items.length === 0 ? (
        <EmptyState
          title="No workflows yet"
          description="Create your first workflow to get started building with Composer."
          action={
            <Button onClick={() => setDialogOpen(true)}>
              <Plus className="mr-1" />
              New workflow
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.items.map((wf) => (
            <DesignerWorkflowCard key={wf.id} wf={wf} />
          ))}
        </div>
      )}

      <NewWorkflowDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}
