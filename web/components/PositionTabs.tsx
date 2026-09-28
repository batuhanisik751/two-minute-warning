import Link from "next/link";
import { POSITIONS } from "@/lib/method";
import { waiversHref, type ListKey } from "@/lib/params";

/** Position tabs as links (they work without JavaScript and can be shared). */
export default function PositionTabs({ current, chosen }: { current: string; chosen: ListKey | null }) {
  return (
    <nav aria-label="Position" className="mb-4">
      <ul className="flex flex-wrap gap-2">
        {POSITIONS.map((p) => {
          const active = p === current;
          return (
            <li key={p}>
              <Link
                href={waiversHref({ pos: p, season: chosen?.season, week: chosen?.week, kind: chosen?.kind })}
                aria-current={active ? "page" : undefined}
                className={`inline-flex min-h-11 min-w-14 items-center justify-center rounded-md border px-4 font-semibold no-underline ${
                  active ? "border-accent bg-accent text-on-accent" : "border-line bg-surface text-fg hover:border-accent"
                }`}
              >
                {p}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
