import type { Metadata, Viewport } from "next";
import { Barlow_Condensed, Inter } from "next/font/google";
import Link from "next/link";
import DataAsOf from "@/components/DataAsOf";
import NavLinks from "@/components/NavLinks";
import SiteFooter from "@/components/SiteFooter";
import ThemeToggle from "@/components/ThemeToggle";
import { THEME_SCRIPT } from "@/lib/theme";
import "./globals.css";

// Both faces are downloaded by `next build` and served from this site (no request to Google
// from the reader's browser). Barlow Condensed: headings, numbers, ranks; Inter: body text.
const display = Barlow_Condensed({
  weight: ["600", "700", "800"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-barlow-condensed",
});
const sans = Inter({ subsets: ["latin"], display: "swap", variable: "--font-inter" });

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
    <html lang="en" suppressHydrationWarning className={`${display.variable} ${sans.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex min-h-screen flex-col antialiased">
        <a className="skip-link" href="#main">
          Skip to main content
        </a>
        <header className="site-strip">
          <div className="strip-bar mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-0 px-4 pt-2 pb-1 md:py-2.5">
            <Link href="/" className="inline-flex min-h-11 min-w-0 items-center gap-2 no-underline sm:gap-2.5">
              <span className="clock" aria-hidden="true">
                2:00
              </span>
              <span className="wordmark">Two-Minute Warning</span>
            </Link>
            <nav aria-label="Main" className="order-3 w-full md:order-2 md:w-auto">
              <NavLinks />
            </nav>
            <div className="order-2 md:order-3">
              <ThemeToggle />
            </div>
          </div>
          <div className="hash-edge" aria-hidden="true" />
          <div className="chyron">
            <div className="mx-auto max-w-6xl px-4 py-2">
              <DataAsOf />
            </div>
          </div>
        </header>
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
          {children}
        </main>
        <SiteFooter />
      </body>
    </html>
  );
}
