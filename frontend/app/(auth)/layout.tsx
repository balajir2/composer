export default function AuthLayout({ children }: { children: React.ReactNode }) {
  // Hero gradient backdrop — same as the home page.
  return (
    <main className="bg-brand-hero flex min-h-screen items-center justify-center p-6">
      {children}
    </main>
  );
}
