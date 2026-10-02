import Link from "next/link";
import type { ListIndexRow } from "@/lib/queries/radar";
import { neighbors, waiversHref, type ListKey, type WeekRef } from "@/lib/params";
import { seasonWeek } from "@/lib/format";

/** The time machine (lite): a season and a week, as a plain GET form (works without
 *  JavaScript), plus links to the neighbouring weeks. /waivers by default (with the position
 *  as a hidden field); another page passes its `action`, its hidden fields and its links. */
export default function WeekPicker({
  index,
  chosen,
  position,
  action = "/waivers",
  hidden,
  hrefFor,
  weekName,
  weekNote,
  submit = "Show list",
}: {
  index: ListIndexRow[];
  chosen: ListKey;
  position?: string;
  action?: string;
  hidden?: Record<string, string>;
  hrefFor?: (w: WeekRef) => string;
  /** a week's own name where "Week N" would mislead (the Hot-Seat end-of-season snapshot) */
  weekName?: (w: WeekRef) => string | null;
  /** the words in brackets after a week (default: its kinds of list; the time machine's
   *  decisions-only weeks have none) */
  weekNote?: (w: WeekRef) => string | null;
  submit?: string;
}) {
  const href = hrefFor ?? ((w: WeekRef) => waiversHref({ pos: position, season: w.season, week: w.week }));
  const fields = hidden ?? (position ? { pos: position } : {});
  const seasons = [...new Set(index.map((r) => r.season))];
  const weeks = new Map<number, Set<string>>();
  for (const r of index.filter((x) => x.season === chosen.season)) {
    if (!weeks.has(r.week)) weeks.set(r.week, new Set());
    weeks.get(r.week)!.add(r.kind);
  }
  const weekOptions = [...weeks.entries()].sort((a, b) => a[0] - b[0]);
  const { newer, older } = neighbors(index, chosen);
  const named = (w: WeekRef) => {
    const n = weekName?.(w);
    return n ? `${w.season}, ${n.toLowerCase()}` : seasonWeek(w.season, w.week);
  };
  const kinds = (k: Set<string>) =>
    k.has("live") && k.has("backtest") ? "live and reconstructed" : k.has("live") ? "live" : "reconstructed";
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
      <form method="get" action={action} className="flex flex-wrap items-end gap-3" aria-label="Choose a week">
        {Object.entries(fields).map(([k, v]) => (
          <input key={k} type="hidden" name={k} value={v} />
        ))}
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
                {weekName?.({ season: chosen.season, week: w }) ?? `Week ${w}`} ({weekNote?.({ season: chosen.season, week: w }) ?? kinds(k)})
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="min-h-11 rounded-md bg-accent px-4 text-sm font-semibold text-on-accent hover:opacity-90"
        >
          {submit}
        </button>
      </form>
      <nav aria-label="Neighbouring weeks" className="flex gap-3 text-sm">
        {older ? (
          <Link href={href(older)} className="inline-flex min-h-11 items-center">
            <span aria-hidden="true">&larr;&nbsp;</span>
            {named(older)}
          </Link>
        ) : null}
        {newer ? (
          <Link href={href(newer)} className="inline-flex min-h-11 items-center">
            {named(newer)}
            <span aria-hidden="true">&nbsp;&rarr;</span>
          </Link>
        ) : null}
      </nav>
    </div>
  );
}
