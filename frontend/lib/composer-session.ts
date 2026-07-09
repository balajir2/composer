import { auth } from "@/auth";

/**
 * Requires *some* session to exist, redirecting anonymous visitors to
 * /login. Unlike requireSession(), this does NOT redirect
 * mustChangePassword sessions away — use it for the /change-password route
 * itself, which must remain reachable by both normal and forced-reset
 * sessions. (Calling requireSession() from /change-password would redirect
 * a mustChangePassword session to /change-password, i.e. the same route it
 * is already on.)
 */
export async function requireAnySession() {
  const session = await auth();
  if (!session) {
    const { redirect } = await import("next/navigation");
    redirect("/login");
  }
  return session;
}

export async function requireSession() {
  const session = await requireAnySession();
  // Defense-in-depth: server components that don't go through the
  // middleware matcher (or any request that slips past it) still get the
  // forced-password-reset gate applied here.
  if ((session as { mustChangePassword?: boolean }).mustChangePassword) {
    const { redirect } = await import("next/navigation");
    redirect("/change-password");
  }
  return session;
}

export async function requireRole(...allowedRoles: Array<"admin" | "member">) {
  const session = await requireSession();
  const role = (session as { role?: "admin" | "member" }).role ?? "member";
  if (!allowedRoles.includes(role)) {
    const { redirect } = await import("next/navigation");
    redirect("/runs"); // member-safe fallback
  }
  return { session, role };
}

export function getAccessToken(session: unknown): string | null {
  if (!session) return null;
  return (session as { accessToken?: string }).accessToken ?? null;
}
