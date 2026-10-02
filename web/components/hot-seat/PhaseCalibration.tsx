import { fmtInt, pct } from "@/lib/format";
import type { CalGroup } from "@/lib/hot-seat";

/** The early-season check as one small table per season phase: within each probability band,
 *  the coach-weeks behind it, the average estimate and how often those coaches were really let go.
 *  Small tables (one band per row) so a phone reads them without scrolling sideways. */
export default function PhaseCalibration({ groups, idPrefix = "cal" }: { groups: CalGroup[]; idPrefix?: string }) {
  return (
    <div className="grid gap-4 md:grid-cols-2" data-testid="phase-calibration">
      {groups.map((g) => (
        <div key={g.phase} className="table-scroll min-w-0 rounded-lg border border-line bg-surface p-2" data-phase={g.phase}>
          <table className="data-table" aria-describedby={`${idPrefix}-note`}>
            <caption className="mb-1 text-left font-display text-base font-bold tracking-wide uppercase">{g.name}</caption>
            <thead>
              <tr>
                <th scope="col">Estimate</th>
                <th scope="col" className="num">
                  Average estimate
                </th>
                <th scope="col" className="num">
                  Really let go
                </th>
              </tr>
            </thead>
            <tbody>
              {g.cells.map((c) => (
                <tr key={c.band} data-band={c.band}>
                  <th scope="row" className="tnum">
                    {c.bandName}
                  </th>
                  <td className="num">{pct(c.meanPred)}</td>
                  <td className="num">
                    <span className="font-semibold">{pct(c.observed)}</span>
                    <span className="block text-xs text-muted">
                      {fmtInt(c.departed)} of {fmtInt(c.n)} ({fmtInt(c.coachSeasons)} {c.coachSeasons === 1 ? "coach-season" : "coach-seasons"})
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
