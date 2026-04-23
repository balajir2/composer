"use client";

import Link from "next/link";
import { signOut, useSession } from "next-auth/react";
import { Button } from "@/components/ui/button";
import { RoleNav } from "./role-nav";

export function AppShell({
  role,
  children,
}: {
  role: "designer" | "runs" | "admin";
  children: React.ReactNode;
}) {
  const { data: session } = useSession();
  const userRole = (session as unknown as { role?: "admin" | "member" })?.role ?? "member";

  return (
    <div className="flex min-h-screen">
      <aside className="bg-muted/20 w-64 border-r p-4">
        <Link href="/" className="block pb-6 text-lg font-semibold">
          Composer
        </Link>
        <RoleNav role={role} />
        <div className="pt-6">
          {userRole === "admin" && role !== "admin" && (
            <Link
              href="/admin"
              className="block pb-2 text-xs text-muted-foreground hover:underline"
            >
              Admin console →
            </Link>
          )}
          {role !== "designer" && (
            <Link
              href="/designer"
              className="block pb-2 text-xs text-muted-foreground hover:underline"
            >
              Designer →
            </Link>
          )}
          {role !== "runs" && (
            <Link href="/runs" className="block pb-2 text-xs text-muted-foreground hover:underline">
              Run workflows →
            </Link>
          )}
        </div>
      </aside>
      <div className="flex-1">
        <header className="flex items-center justify-between border-b bg-background px-6 py-3">
          <h1 className="text-sm font-medium capitalize">{role}</h1>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-muted-foreground">{session?.user?.email}</span>
            <Button variant="outline" size="sm" onClick={() => signOut({ callbackUrl: "/login" })}>
              Sign out
            </Button>
          </div>
        </header>
        <main className="p-6">{children}</main>
      </div>
    </div>
  );
}
