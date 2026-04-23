"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = { href: string; label: string };

const DESIGNER_NAV: NavItem[] = [{ href: "/designer", label: "Workflows" }];
const RUNS_NAV: NavItem[] = [
  { href: "/runs", label: "Run a workflow" },
  { href: "/runs/history", label: "History" },
  { href: "/runs/api-keys", label: "API keys" },
];
const ADMIN_NAV: NavItem[] = [
  { href: "/admin", label: "Dashboard" },
  { href: "/admin/users", label: "Users" },
  { href: "/admin/mcp-servers", label: "MCP servers" },
  { href: "/admin/tools", label: "Built-in tools" },
  { href: "/admin/llm-keys", label: "LLM keys" },
  { href: "/admin/llm-models", label: "LLM models" },
  { href: "/admin/workflows", label: "Workflows" },
];

export function RoleNav({ role }: { role: "designer" | "runs" | "admin" }) {
  const pathname = usePathname();
  const items = role === "designer" ? DESIGNER_NAV : role === "admin" ? ADMIN_NAV : RUNS_NAV;
  return (
    <nav className="flex flex-col gap-1">
      {items.map((it) => {
        const active = pathname === it.href || pathname.startsWith(it.href + "/");
        return (
          <Link
            key={it.href}
            href={it.href}
            className={
              "rounded-md px-3 py-2 text-sm transition-colors " +
              (active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground")
            }
          >
            {it.label}
          </Link>
        );
      })}
    </nav>
  );
}
