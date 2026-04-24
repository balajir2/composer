import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

// Bounteous brand font — matches the Bounteous AI Efficiency design system
// (public/shared/styles.css in that repo).  Exposes --font-sans for
// tailwind.config.ts.
const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
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
