import { requireSession } from "@/lib/composer-session";
import { AppShell } from "@/components/composer/app-shell";
import { ErrorBoundary } from "@/components/composer/error-boundary";

export default async function RunsLayout({ children }: { children: React.ReactNode }) {
  await requireSession();
  return (
    <AppShell role="runs">
      <ErrorBoundary>{children}</ErrorBoundary>
    </AppShell>
  );
}
