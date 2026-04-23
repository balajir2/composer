import { requireSession } from "@/lib/composer-session";
import { AppShell } from "@/components/composer/app-shell";

export default async function RunsLayout({ children }: { children: React.ReactNode }) {
  await requireSession();
  return <AppShell role="runs">{children}</AppShell>;
}
