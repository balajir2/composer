"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { listExecutions } from "@/lib/api/executions";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";

export default function HistoryPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["executions"],
    queryFn: () => listExecutions({ limit: 100 }),
  });

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (!data || data.items.length === 0) {
    return (
      <EmptyState title="No runs yet" description="Executions you trigger will show up here." />
    );
  }

  return (
    <div>
      <h2 className="pb-6 text-2xl font-semibold">History</h2>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Execution</TableHead>
            <TableHead>Workflow</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Started</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.map((e) => (
            <TableRow key={e.id}>
              <TableCell className="font-mono text-xs">
                <Link
                  href={`/runs/${e.workflowId}/executions/${e.id}`}
                  className="text-primary hover:underline"
                >
                  {e.id}
                </Link>
              </TableCell>
              <TableCell className="text-sm">{e.workflowId}</TableCell>
              <TableCell>
                <Badge variant={e.status === "failed" ? "destructive" : "secondary"}>
                  {e.status}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground text-xs">
                {new Date(String(e.startedAt)).toLocaleString()}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
