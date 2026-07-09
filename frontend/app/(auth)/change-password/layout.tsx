import { requireAnySession } from "@/lib/composer-session";

// Guards against anonymous (no-session) visitors before the change-password
// form renders. Deliberately uses requireAnySession() rather than
// requireSession() — the latter redirects mustChangePassword sessions to
// /change-password, which would be a redirect to this very route. Both
// normal sessions and mustChangePassword sessions must be able to reach
// this page; only a completely absent session should be redirected away
// (to /login).
export default async function ChangePasswordLayout({ children }: { children: React.ReactNode }) {
  await requireAnySession();
  return children;
}
