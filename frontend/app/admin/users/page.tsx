"use client";

import { useQuery } from "@tanstack/react-query";
import { listAdminUsers } from "@/lib/api/admin";
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
import { UserActiveToggle } from "@/components/composer/user-active-toggle";
import { UserRoleToggle } from "@/components/composer/user-role-toggle";
import { ResetPasswordDialog } from "@/components/composer/reset-password-dialog";

export default function AdminUsersPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => listAdminUsers(),
  });

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">Users</h2>
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : isError || !data ? (
        <EmptyState
          title="Could not load users"
          description="Try refreshing the page. If this keeps happening, check your network."
        />
      ) : data.length === 0 ? (
        <EmptyState
          title="No users yet"
          description="Users will appear here once they have registered."
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Display name</TableHead>
              <TableHead>Role</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="w-64 text-right"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((user) => {
              const active = user.isActive !== false;
              return (
                <TableRow key={user.id}>
                  <TableCell>{user.email}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {user.displayName ?? "—"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={user.role === "admin" ? "default" : "secondary"}>
                      {user.role}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={active ? "secondary" : "destructive"}>
                      {active ? "active" : "inactive"}
                    </Badge>
                  </TableCell>
                  <TableCell className="flex items-center justify-end gap-2">
                    <UserRoleToggle user={user} />
                    <UserActiveToggle user={user} />
                    <ResetPasswordDialog user={user} />
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
