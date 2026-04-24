export default function AuthLayout({ children }: { children: React.ReactNode }) {
  // Bounteous hero gradient backdrop — same as the rebrand plan's home page.
  return (
    <main className="bg-brand-hero flex min-h-screen items-center justify-center p-6">
      {children}
    </main>
  );
}
