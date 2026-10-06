import Term from "@/components/Term";
import { fmtTendency, fmtVsLeague, isTendencyMetric, METRIC_TEXT, TENDENCY_METRICS } from "@/lib/coach-tendencies";
import type { CareerRow } from "@/lib/queries/coach-tendencies";

const span = (a: number, b: number) => (a === b ? `${a}` : `${a}–${b}`);

/** His career line: every completed season pooled, against the league of the seasons he coached
 *  (weighted by his snaps each season), one row per metric. */
export default function CareerTable({ rows, name }: { rows: CareerRow[]; name: string }) {
  const byMetric = new Map(rows.filter((r) => isTendencyMetric(r.metric)).map((r) => [r.metric, r]));
  const ordered = TENDENCY_METRICS.filter((m) => byMetric.has(m)).map((m) => byMetric.get(m)!);
  if (!ordered.length) return null;
  const first = Math.min(...ordered.map((r) => r.firstSeason));
  const last = Math.max(...ordered.map((r) => r.lastSeason));
  const teams = ordered.reduce((a, r) => (r.teams.length > a.length ? r.teams : a), "");
  return (
    <div className="mt-6" data-testid="tendency-career">
      <h3 className="mb-2 font-display text-lg font-bold">
        Career, {span(first, last)} ({teams.split("/").join(", ")})
      </h3>
      <p className="mb-2 text-sm text-muted">Completed regular seasons pooled; the league in the same seasons, weighted by his snaps each season.</p>
      <div className="table-scroll">
        <table className="data-table">
          <caption className="sr-only">{name}&apos;s career tendencies against the league of his seasons</caption>
          <thead>
            <tr>
              <th scope="col">Tendency</th>
              <th scope="col" className="num">
                His offenses
              </th>
              <th scope="col" className="num">
                League
              </th>
              <th scope="col" className="num">
                Difference
              </th>
              <th scope="col" className="num">
                Seasons
              </th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((r) => (
              <tr key={r.metric} data-row="" data-metric={r.metric}>
                <th scope="row" data-cell="metric">
                  <Term name={r.metric}>{METRIC_TEXT[r.metric as keyof typeof METRIC_TEXT].short}</Term>
                </th>
                <td className="num" data-cell="value">
                  {fmtTendency(r.metric, r.value)}
                </td>
                <td className="num" data-cell="league">
                  {fmtTendency(r.metric, r.leagueAvg)}
                </td>
                <td className="num" data-cell="diff">
                  {fmtVsLeague(r.metric, r.vsLeague)}
                </td>
                <td className="num whitespace-nowrap" data-cell="seasons">
                  {span(r.firstSeason, r.lastSeason)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
