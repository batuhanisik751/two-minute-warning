"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export const NAV = [
  { href: "/", label: "This week" },
  { href: "/waivers", label: "Waivers" },
  { href: "/regression", label: "Regression" },
  { href: "/methodology", label: "Methodology" },
] as const;

/** The main navigation on the scoreboard strip; the current page has a gold marker under it. */
export default function NavLinks() {
  const path = usePathname() ?? "/";
  return (
    <ul className="flex flex-wrap gap-x-0.5 gap-y-0.5 sm:gap-1">
      {NAV.map((l) => {
        const active = l.href === "/" ? path === "/" : path === l.href || path.startsWith(`${l.href}/`);
        return (
          <li key={l.href}>
            <Link
              href={l.href}
              aria-current={active ? "page" : undefined}
              className="nav-link inline-flex min-h-11 items-center rounded-sm px-2 no-underline sm:px-3"
            >
              {l.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
