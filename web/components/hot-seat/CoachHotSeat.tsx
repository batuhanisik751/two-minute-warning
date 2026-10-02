import Link from "next/link";
import ChartFigure from "@/components/charts/ChartFigure";
import { FoldTable } from "@/components/Fold";
import Term from "@/components/Term";
import { fmtInt } from "@/lib/format";
import { hotSeatHref, lastPerSeason, mergeTimeline, outcomeWords, timelineWords, wholePct } from "@/lib/hot-seat";
import { HOT_SEAT_WINDOW_DAYS } from "@/lib/method";
import type { CoachHotSeatRow } from "@/lib/queries/hot-seat";
import { OutcomeTag } from "./parts";

/** The coach page's Hot-Seat history: this season's weekly estimates (a chart with the numbers as
 *  a table) and, for past seasons, his last estimate (the end-of-season snapshot, else his last
 *  weekly list) with what really happened. Nothing when the coach has no Hot-Seat row. */
export default function CoachHotSeat({ rows, name, season }: { rows: CoachHotSeatRow[]; name: string; season: number | null }) {
  if (!rows.length) return null;
  const now = season !== null ? rows.filter((r) => r.season === season) : [];
  const points = mergeTimeline(now.map((r) => ({ ...r, coachId: "me" }))).get("me") ?? [];
  const past = lastPerSeason(rows.filter((r) => r.season !== season));
  return (
    <section aria-labelledby="coach-hot-seat-heading" className="mt-10" data-testid="coach-hot-seat">
      <h2 id="coach-hot-seat-heading" className="section-title mb-2 scroll-mt-24">
        Hot-Seat history
      </h2>
      <p className="mb-3 max-w-3xl text-sm text-muted">
        The <Term name="hot_seat_estimate">estimated chance</Term> of being <Term name="hot_seat_let_go">let go</Term> by{" "}
        {HOT_SEAT_WINDOW_DAYS} days after the season: an estimate from past seasons&apos; patterns, not a verdict.{" "}
        <Link href="/hot-seat">All coaches</Link>
      </p>
      {points.length ? (
        <div className="space-y-2" data-testid="coach-hot-seat-season">
          <p>
            <strong>{season}:</strong> {timelineWords(points)}.
          </p>
          <ChartFigure
            id="coach-hot-seat-chart"
            caption={`${name}: estimated chance by week, ${season}`}
            data={points.map((p) => ({ week: p.week, estimate: p.probability }))}
            series={[{ key: "estimate", name: "Estimated chance", kind: "line", color: "var(--chart-a)" }]}
            columns={[{ key: "estimate", label: "Estimated chance", format: (v) => (v === null ? "–" : wholePct(v)) }]}
            percent
            testId="hot-seat-chart"
          />
        </div>
      ) : null}
      {past.length ? (
        <div className="mt-6" data-testid="coach-hot-seat-past">
          <h3 className="display mb-2 text-xl uppercase">Past seasons</h3>
          <p className="mb-2 text-sm text-muted">
            His last estimate of each season (the end-of-season snapshot, or his last weekly list) and what happened.
            &ldquo;Not let go&rdquo;: no firing or mutual parting announced by {HOT_SEAT_WINDOW_DAYS} days after the season.
          </p>
          <FoldTable
            label={`${name}'s past Hot-Seat seasons`}
            rows={past.map((r) => (
              <tr key={r.season}>
                <th scope="row">
                  <Link href={hotSeatHref({ season: r.season, week: r.week })}>{r.season}</Link>
                  <span className="block text-xs font-normal text-muted">
                    {r.team}
                    {r.isInterim ? ", interim" : ""}
                  </span>
                </th>
                <td className="num">
                  <span className="font-semibold">{wholePct(r.probability)}</span>
                  <span className="block text-xs text-muted">{r.snapshot === "end_of_season" ? "end of season" : `week ${r.week}, his last list`}</span>
                </td>
                <td className="num whitespace-nowrap">
                  {fmtInt(r.rank)} of {fmtInt(r.nCoaches)}
                </td>
                <td>
                  <OutcomeTag outcome={r.outcome} long={outcomeWords(r.outcome).tone !== "not-let-go"} />
                </td>
              </tr>
            ))}
            table={(body) => (
              <div className="table-scroll">
                <table className="data-table" data-testid="coach-hot-seat-table">
                  <caption className="sr-only">{name}&apos;s last estimate of each past season, his team and what happened</caption>
                  <thead>
                    <tr>
                      <th scope="col">Season</th>
                      <th scope="col" className="num">Last estimate</th>
                      <th scope="col" className="num">Rank</th>
                      <th scope="col">What happened</th>
                    </tr>
                  </thead>
                  {body}
                </table>
              </div>
            )}
          />
        </div>
      ) : null}
    </section>
  );
}
