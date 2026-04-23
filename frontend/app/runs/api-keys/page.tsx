"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listApiKeys, revokeApiKey } from "@/lib/api/api-keys";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/composer/empty-state";
import { ApiKeyCreateDialog } from "@/components/composer/api-key-create-dialog";
import { toast } from "sonner";

export default function ApiKeysPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["api-keys"],
    queryFn: () => listApiKeys(),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeApiKey(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
      toast.success("Key revoked.");
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold">API keys</h2>
        <ApiKeyCreateDialog />
      </div>
      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : !data || data.length === 0 ? (
        <EmptyState
          title="No API keys yet"
          description="Create one to invoke production workflows from outside the UI."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Label</TableHead>
              <TableHead>Prefix</TableHead>
              <TableHead>Last used</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="w-24"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((k) => {
              const revoked = Boolean(k.revokedAt);
              return (
                <TableRow key={k.id}>
                  <TableCell>{k.label}</TableCell>
                  <TableCell className="font-mono text-xs">{k.keyPrefix}…</TableCell>
                  <TableCell className="text-muted-foreground text-xs">
                    {k.lastUsedAt ? new Date(k.lastUsedAt).toLocaleString() : "never"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={revoked ? "destructive" : "secondary"}>
                      {revoked ? "revoked" : "active"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {!revoked && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => revoke.mutate(k.id)}
                        disabled={revoke.isPending}
                      >
                        Revoke
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
