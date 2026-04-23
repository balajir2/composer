export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="bg-muted/30 flex min-h-screen items-center justify-center">{children}</main>
  );
}
