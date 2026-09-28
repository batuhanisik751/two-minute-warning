import Link from "next/link";
import type { ListIndexRow } from "@/lib/queries/radar";
import { neighbors, waiversHref, type ListKey } from "@/lib/params";
import { seasonWeek } from "@/lib/format";

/** The time machine (lite): a season and a week, as a plain GET form (works without
 *  JavaScript), plus links to the neighbouring weeks. */
export default function WeekPicker({
  index,
  chosen,
  position,
}: {
  index: ListIndexRow[];
  chosen: ListKey;
  position: string;
}) {
  const seasons = [...new Set(index.map((r) => r.season))];
  const weeks = new Map<number, Set<string>>();
  for (const r of index.filter((x) => x.season === chosen.season)) {
    if (!weeks.has(r.week)) weeks.set(r.week, new Set());
    weeks.get(r.week)!.add(r.kind);
  }
  const weekOptions = [...weeks.entries()].sort((a, b) => a[0] - b[0]);
  const { newer, older } = neighbors(index, chosen);
  const kinds = (k: Set<string>) =>
    k.has("live") && k.has("backtest") ? "live and reconstructed" : k.has("live") ? "live" : "reconstructed";
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
      <form method="get" action="/waivers" className="flex flex-wrap items-end gap-3" aria-label="Choose a week">
        <input type="hidden" name="pos" value={position} />
        <label className="flex flex-col text-sm font-medium">
          Season
          <select
            name="season"
            defaultValue={String(chosen.season)}
            className="mt-1 min-h-11 rounded-md border border-line bg-surface px-2 text-fg"
          >
            {seasons.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm font-medium">
          Week
          <select
            name="week"
            defaultValue={String(chosen.week)}
            className="mt-1 min-h-11 rounded-md border border-line bg-surface px-2 text-fg"
          >
            {weekOptions.map(([w, k]) => (
              <option key={w} value={w}>
                Week {w} ({kinds(k)})
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="min-h-11 rounded-md bg-accent px-4 text-sm font-semibold text-on-accent hover:opacity-90"
        >
          Show list
        </button>
      </form>
      <nav aria-label="Neighbouring weeks" className="flex gap-3 text-sm">
        {older ? (
          <Link href={waiversHref({ pos: position, season: older.season, week: older.week })} className="inline-flex min-h-11 items-center">
            <span aria-hidden="true">&larr;&nbsp;</span>
            {seasonWeek(older.season, older.week)}
          </Link>
        ) : null}
        {newer ? (
          <Link href={waiversHref({ pos: position, season: newer.season, week: newer.week })} className="inline-flex min-h-11 items-center">
            {seasonWeek(newer.season, newer.week)}
            <span aria-hidden="true">&nbsp;&rarr;</span>
          </Link>
        ) : null}
      </nav>
    </div>
  );
}
