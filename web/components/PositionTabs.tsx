import Link from "next/link";
import { waiversHref, type ListKey } from "@/lib/params";

/** Position tabs as links (they work without JavaScript and can be shared). The tabs are the
 *  positions the data has (lib/positions.ts tabPositions): a position a later step publishes
 *  appears here on its own, and none is shown before it has a list. */
export default function PositionTabs({
  positions,
  current,
  chosen,
}: {
  positions: readonly string[];
  current: string;
  chosen: ListKey | null;
}) {
  return (
    <nav aria-label="Position" className="mb-5">
      <ul className="flex flex-wrap gap-1.5 sm:gap-2">
        {positions.map((p) => {
          const active = p === current;
          return (
            <li key={p}>
              <Link
                href={waiversHref({ pos: p, season: chosen?.season, week: chosen?.week, kind: chosen?.kind })}
                aria-current={active ? "page" : undefined}
                data-pos={p}
                className="pos-tab inline-flex min-h-11 min-w-14 items-center justify-center rounded-md px-3 no-underline sm:min-w-16 sm:px-4"
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
