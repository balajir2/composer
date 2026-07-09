"use client";

import Link from "next/link";
import { signOut, useSession } from "next-auth/react";
import { Button } from "@/components/ui/button";
import { ComposerLogo } from "./composer-logo";
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
      {/* Bounteous-branded sidebar: vertical deep-purple gradient, white
          text, pink active indicator.  Mirrors the brand system from
          Bounteous AI Efficiency (public/shared/styles.css). */}
      <aside className="flex w-64 flex-col bg-brand-gradient">
        <Link
          href="/"
          className="flex items-center gap-3 border-b border-white/10 px-5 pb-5 pt-6 text-white"
        >
          <ComposerLogo size={28} />
          <span className="text-[0.8rem] font-extrabold tracking-[1.5px]">
            COMPOSER
          </span>
        </Link>

        <div className="flex-1 overflow-y-auto py-4">
          <RoleNav userRole={userRole} />
        </div>

        <div className="border-t border-white/10 px-5 py-4 text-xs">
          <div className="truncate font-semibold text-white/65">
            {session?.user?.email}
          </div>
          <Link
            href="/change-password"
            className="mt-1 block text-[0.7rem] text-white/40 hover:text-white/90"
          >
            Change password
          </Link>
          <button
            type="button"
            onClick={() => signOut({ callbackUrl: "/login" })}
            className="mt-1 text-[0.7rem] text-white/40 hover:text-white/90"
          >
            Sign out
          </button>
        </div>
      </aside>

      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b bg-background px-6 py-3">
          <h1 className="text-sm font-semibold capitalize">{role}</h1>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-muted-foreground">{session?.user?.email}</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => signOut({ callbackUrl: "/login" })}
            >
              Sign out
            </Button>
          </div>
        </header>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
