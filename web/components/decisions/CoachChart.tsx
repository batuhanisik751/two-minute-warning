import SeasonChart from "@/components/charts/SeasonChart";
import { aggressivenessWords, wpPoints } from "@/lib/decisions";
import type { CoachSeasonRow } from "@/lib/queries/decisions";

/** The coach page's small chart: WP lost per game (bars) and aggressiveness (a line) by season,
 *  two panels in one figure, with the same numbers as a table inside a <details> (the accessible
 *  alternative; in the page even while closed). */
export default function CoachChart({ rows, name }: { rows: CoachSeasonRow[]; name: string }) {
  const caption = `${name}: WP lost per game and aggressiveness by season`;
  const lost = rows.map((r) => ({ season: r.season, value: r.wpLostPerGame }));
  const aggr = rows.map((r) => ({ season: r.season, value: r.aggressiveness }));
  return (
    <figure aria-labelledby="coach-chart-caption" className="rounded-lg border border-line bg-surface p-3" data-testid="chart">
      <figcaption id="coach-chart-caption" className="mb-2 font-display text-lg font-bold tracking-wide uppercase">
        {caption}
      </figcaption>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="min-w-0">
          <p className="mb-1 text-sm font-medium">WP lost per game (WP points; lower is better)</p>
          <SeasonChart data={lost} kind="bar" color="var(--chart-b)" name="WP lost per game" title={`${name}: WP lost per game by season`} desc="WP points per game, by season. The same numbers are in the table below." />
        </div>
        <div className="min-w-0">
          <p className="mb-1 text-sm font-medium">Aggressiveness (went for it when going was clearly best)</p>
          <SeasonChart data={aggr} kind="line" color="var(--chart-a)" name="Aggressiveness" title={`${name}: aggressiveness by season`} desc="The share of clear go calls the coach went for, by season. The same numbers are in the table below." percent />
        </div>
      </div>
      <details className="mt-2 text-sm">
        <summary className="inline-flex min-h-11 items-center font-medium text-accent">Show the numbers as a table</summary>
        <div className="table-scroll mt-2">
          <table className="data-table">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr>
                <th scope="col">Season</th>
                <th scope="col" className="num">
                  WP lost per game
                </th>
                <th scope="col">Aggressiveness</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.season}>
                  <th scope="row">{r.season}</th>
                  <td className="num">{wpPoints(r.wpLostPerGame, 2)}</td>
                  <td>{aggressivenessWords(r.goClearWent, r.goClear)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
