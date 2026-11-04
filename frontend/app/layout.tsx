import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { Providers } from "./providers";

// Brand font — exposes --font-sans for tailwind.config.ts. Self-hosted (variable
// latin subset) so builds never depend on fonts.googleapis.com being reachable.
const jakarta = localFont({
  src: "./fonts/PlusJakartaSans-latin.woff2",
  weight: "400 800",
  display: "swap",
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: "Composer",
  description: "Enterprise workflow composer powered by LangGraph.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${jakarta.variable} font-sans`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
