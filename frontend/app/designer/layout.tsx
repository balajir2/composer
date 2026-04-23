import { requireSession } from "@/lib/composer-session";
import { AppShell } from "@/components/composer/app-shell";

export default async function DesignerLayout({ children }: { children: React.ReactNode }) {
  await requireSession(); // any authenticated user may access designer; fine-grained permissions TBD
  return <AppShell role="designer">{children}</AppShell>;
}
