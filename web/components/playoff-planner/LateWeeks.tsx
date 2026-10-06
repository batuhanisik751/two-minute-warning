import { pct } from "@/lib/format";
import { lateEras, PLANNER_NOTE, type PPLateWeek } from "@/lib/playoff-planner";

/** The late-season resting note: per era, the share of regular QB / RB / WR / TE who played in
 *  the last two playoff weeks (playoff_planner_late_weeks), then weekly.py's NOTE. */
export default function LateWeeks({ rows, weeks }: { rows: PPLateWeek[]; weeks: number[] }) {
  const eras = lateEras(rows, weeks);
  const [before, last] = [weeks[weeks.length - 2], weeks[weeks.length - 1]];
  return (
    <div className="max-w-3xl space-y-2" data-testid="pp-late">
      <h3 className="font-display text-lg font-bold tracking-wide uppercase">Teams resting starters</h3>
      {eras.length ? (
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {eras.map((e) => (
            <li key={e.era} data-era={e.era}>
              <span className="font-semibold">{e.era}:</span> regular players who played in week {before}, then week {last}:{" "}
              {e.rows.map((r) => `${r.position} ${pct(r.before)} to ${pct(r.last)}`).join(", ")}.
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-sm text-muted">{PLANNER_NOTE}</p>
    </div>
  );
}
