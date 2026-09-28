import type { Metadata, Viewport } from "next";
import Link from "next/link";
import DataAsOf from "@/components/DataAsOf";
import NavLinks from "@/components/NavLinks";
import SiteFooter from "@/components/SiteFooter";
import ThemeToggle from "@/components/ThemeToggle";
import { THEME_SCRIPT } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Two-Minute Warning", template: "%s · Two-Minute Warning" },
  description:
    "An open, point-in-time NFL early-warning app: the Waiver Radar and its public track record.",
  // Not indexed until launch (E5): robots.txt disallows everything as well.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  colorScheme: "light dark",
};

// Every page reads Postgres per request (through the data cache, lib/cache.ts); nothing is
// rendered or read at build time.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex min-h-screen flex-col antialiased">
        <a className="skip-link" href="#main">
          Skip to main content
        </a>
        <header className="border-b border-line bg-surface">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
            <Link href="/" className="text-lg font-bold tracking-tight text-fg no-underline">
              Two-Minute Warning
            </Link>
            <nav aria-label="Main" className="order-3 w-full sm:order-2 sm:w-auto">
              <NavLinks />
            </nav>
            <div className="order-2 sm:order-3">
              <ThemeToggle />
            </div>
          </div>
          <div className="mx-auto max-w-5xl px-4 pb-3">
            <DataAsOf />
          </div>
        </header>
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
          {children}
        </main>
        <SiteFooter />
      </body>
    </html>
  );
}
