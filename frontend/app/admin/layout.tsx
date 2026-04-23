import { requireRole } from "@/lib/composer-session";
import { AppShell } from "@/components/composer/app-shell";
import { ErrorBoundary } from "@/components/composer/error-boundary";

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  await requireRole("admin");
  return (
    <AppShell role="admin">
      <ErrorBoundary>{children}</ErrorBoundary>
    </AppShell>
  );
}
