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
  // Styling matches the Bounteous design system sidebar — white-on-purple,
  // pink right-border + pink-alpha background for the active item.  The
  // AppShell wraps this in a gradient aside so these colors sit on top of
  // the brand gradient.
  return (
    <nav className="flex flex-col">
      {items.map((it) => {
        const active = pathname === it.href || pathname.startsWith(it.href + "/");
        return (
          <Link
            key={it.href}
            href={it.href}
            className={
              "flex items-center border-r-2 px-4 py-2 text-[0.82rem] font-medium transition-colors " +
              (active
                ? "border-[var(--brand-pink)] bg-[var(--brand-pink-alpha)] font-bold text-white"
                : "border-transparent text-white/65 hover:bg-white/8 hover:text-white")
            }
          >
            {it.label}
          </Link>
        );
      })}
    </nav>
  );
}
