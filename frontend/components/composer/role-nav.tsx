"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = { href: string; label: string };
type NavGroup = {
  label: string;
  /** The group's "landing" page — the section header itself is clickable. */
  href: string;
  items: NavItem[];
  /** Role gate.  When set, only users with the matching role see the group. */
  adminOnly?: boolean;
};

// Top-level groups, displayed top-to-bottom.  Each renders as a section
// header (uppercase label) with its sub-items indented underneath, so the
// hierarchy reads like a proper menu rather than the previous flat list.
const NAV_GROUPS: NavGroup[] = [
  {
    label: "Designer",
    href: "/designer",
    items: [{ href: "/designer", label: "Workflows" }],
  },
  {
    label: "Run workflows",
    href: "/runs",
    items: [
      { href: "/runs", label: "Run a workflow" },
      { href: "/runs/history", label: "History" },
      { href: "/runs/api-keys", label: "API keys" },
    ],
  },
  {
    label: "Admin",
    href: "/admin",
    adminOnly: true,
    items: [
      { href: "/admin", label: "Dashboard" },
      { href: "/admin/users", label: "Users" },
      { href: "/admin/mcp-servers", label: "MCP servers" },
      { href: "/admin/tools", label: "Built-in tools" },
      { href: "/admin/llm-keys", label: "LLM keys" },
      { href: "/admin/llm-models", label: "LLM models" },
      { href: "/admin/workflows", label: "Workflows" },
    ],
  },
];

function isActive(pathname: string, href: string): boolean {
  if (pathname === href) return true;
  // Stricter prefix-match: only matches when the next character is "/" so
  // "/runs" doesn't claim "/runs-archive" as active.
  return pathname.startsWith(href + "/");
}

export function RoleNav({
  userRole,
}: {
  /** Renders all groups the user has access to.  Members see Designer +
   *  Run workflows; admins additionally see the Admin group. */
  userRole: "admin" | "member";
}) {
  const pathname = usePathname();
  const visible = NAV_GROUPS.filter((g) => !g.adminOnly || userRole === "admin");

  return (
    <nav className="flex flex-col gap-4">
      {visible.map((group) => {
        // The group is "active" if any of its items (or the group's own
        // href) matches the current path.  We use this to highlight the
        // section header so users always know where they are at a glance.
        const groupActive =
          isActive(pathname, group.href) ||
          group.items.some((it) => isActive(pathname, it.href));
        return (
          <div key={group.href} className="flex flex-col">
            <Link
              href={group.href}
              className={
                "px-4 pb-1 pt-0.5 text-[0.65rem] font-bold uppercase tracking-[1.5px] transition-colors " +
                (groupActive ? "text-white" : "text-white/40 hover:text-white/70")
              }
            >
              {group.label}
            </Link>
            <div className="flex flex-col">
              {group.items.map((it) => {
                const active = isActive(pathname, it.href);
                return (
                  <Link
                    key={it.href}
                    href={it.href}
                    className={
                      "flex items-center border-r-2 pl-7 pr-4 py-1.5 text-[0.82rem] transition-colors " +
                      (active
                        ? "border-[var(--brand-pink)] bg-[var(--brand-pink-alpha)] font-semibold text-white"
                        : "border-transparent text-white/65 hover:bg-white/8 hover:text-white")
                    }
                  >
                    {it.label}
                  </Link>
                );
              })}
            </div>
          </div>
        );
      })}
    </nav>
  );
}
