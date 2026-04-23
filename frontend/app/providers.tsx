"use client";

import { SessionProvider } from "next-auth/react";
import { QueryProvider } from "@/lib/query-client";
import { Toaster } from "@/components/ui/sonner";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <QueryProvider>
        {children}
        <Toaster richColors position="top-right" />
      </QueryProvider>
    </SessionProvider>
  );
}
