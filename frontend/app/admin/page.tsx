"use client";

import { useQuery } from "@tanstack/react-query";
import { listAdminUsers } from "@/lib/api/admin";
import { listWorkflows } from "@/lib/api/workflows";
import { listExecutions } from "@/lib/api/executions";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";

function StatCard({
  title,
  value,
  isLoading,
}: {
  title: string;
  value: number | undefined;
  isLoading: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-8 w-16" />
        ) : (
          <p className="text-3xl font-bold">{value ?? 0}</p>
        )}
      </CardContent>
    </Card>
  );
}

export default function AdminDashboard() {
  const usersQ = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => listAdminUsers(),
  });

  const workflowsQ = useQuery({
    queryKey: ["admin-workflows"],
    queryFn: () => listWorkflows({ limit: 100 }),
  });

  const executionsQ = useQuery({
    queryKey: ["admin-executions-recent"],
    queryFn: () => listExecutions({ limit: 20 }),
  });

  const totalUsers = usersQ.data?.length;
  // listWorkflows returns {total, items, limit, offset} — use `total` for the
  // true workflow count (items is capped at `limit` = 100).
  const totalWorkflows = workflowsQ.data?.total;
  const productionCount = workflowsQ.data?.items.filter((wf) => wf.isProduction).length;
  const recentExecutions = executionsQ.data?.total;

  const isLoading = usersQ.isLoading || workflowsQ.isLoading || executionsQ.isLoading;

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold">Admin dashboard</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard title="Total users" value={totalUsers} isLoading={usersQ.isLoading} />
        <StatCard title="Total workflows" value={totalWorkflows} isLoading={workflowsQ.isLoading} />
        <StatCard
          title="Production workflows"
          value={productionCount}
          isLoading={workflowsQ.isLoading}
        />
        <StatCard
          title="Recent executions (last 20)"
          value={recentExecutions}
          isLoading={executionsQ.isLoading}
        />
      </div>
      {!isLoading && (usersQ.isError || workflowsQ.isError || executionsQ.isError) && (
        <EmptyState
          title="Some stats could not be loaded"
          description="Check your connection and try again."
        />
      )}
    </div>
  );
}
