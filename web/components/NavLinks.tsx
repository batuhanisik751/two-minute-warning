"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export const NAV = [
  { href: "/", label: "This week" },
  { href: "/waivers", label: "Waivers" },
  { href: "/regression", label: "Regression" },
  { href: "/methodology", label: "Methodology" },
] as const;

export default function NavLinks() {
  const path = usePathname() ?? "/";
  return (
    <ul className="flex flex-wrap gap-x-0.5 gap-y-1 sm:gap-1">
      {NAV.map((l) => {
        const active = l.href === "/" ? path === "/" : path === l.href || path.startsWith(`${l.href}/`);
        return (
          <li key={l.href}>
            <Link
              href={l.href}
              aria-current={active ? "page" : undefined}
              className={`inline-flex min-h-11 items-center rounded-md px-2 text-sm font-medium no-underline sm:px-3 ${
                active ? "bg-accent text-on-accent" : "text-fg hover:bg-raised"
              }`}
            >
              {l.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
