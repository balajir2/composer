"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { updateUserRole, type AdminUser } from "@/lib/api/admin";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

export function UserRoleToggle({ user }: { user: AdminUser }) {
  const qc = useQueryClient();
  const nextRole = user.role === "admin" ? "member" : "admin";

  const mutation = useMutation({
    mutationFn: () => updateUserRole(user.id, nextRole),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin-users"] });
      toast.success(
        nextRole === "admin"
          ? `${user.email} promoted to admin.`
          : `${user.email} demoted to member.`
      );
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "Role update failed."),
  });

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => mutation.mutate()}
      disabled={mutation.isPending}
    >
      {mutation.isPending
        ? "Updating…"
        : nextRole === "admin"
          ? "Promote to admin"
          : "Demote to member"}
    </Button>
  );
}
