import { requireSession } from "@/lib/composer-session";
import { AppShell } from "@/components/composer/app-shell";
import { ErrorBoundary } from "@/components/composer/error-boundary";

export default async function DesignerLayout({ children }: { children: React.ReactNode }) {
  await requireSession(); // any authenticated user may access designer; fine-grained permissions TBD
  return (
    <AppShell role="designer">
      <ErrorBoundary>{children}</ErrorBoundary>
    </AppShell>
  );
}
