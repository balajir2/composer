import { requireRole } from "@/lib/composer-session";
import { AppShell } from "@/components/composer/app-shell";

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  await requireRole("admin");
  return <AppShell role="admin">{children}</AppShell>;
}
