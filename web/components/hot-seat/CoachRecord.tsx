import Link from "next/link";
import { FoldTable } from "@/components/Fold";
import Term from "@/components/Term";
import { hotSeatHref, recordPerSeason, signedNum } from "@/lib/hot-seat";
import type { CoachHotSeatRow } from "@/lib/queries/hot-seat";

/** The coach page's record vs expectation (PROJECT_SPEC 9.1): per season his regular-season
 *  record and the wins the betting market expected, from his published Hot-Seat rows
 *  (lib/hot-seat.ts recordPerSeason). Nothing when the coach has no Hot-Seat row. */
export default function CoachRecord({ rows, name }: { rows: CoachHotSeatRow[]; name: string }) {
  const seasons = recordPerSeason(rows);
  if (!seasons.length) return null;
  return (
    <section aria-labelledby="coach-record-heading" className="mt-10" data-testid="coach-record">
      <h2 id="coach-record-heading" className="section-title mb-2 scroll-mt-24">
        Record vs expectation
      </h2>
      <p className="mb-3 max-w-3xl text-sm text-muted">
        His regular-season record against the <Term name="expected_wins">wins the betting market expected</Term> from each
        game&apos;s closing odds, and the difference (<Term name="wins_vs_expected">wins vs market expectation</Term>): the
        season&apos;s end, or the newest week of a season in progress or one he did not finish. Seasons the Hot-Seat
        Meter covers only. Newest first.
      </p>
      <FoldTable
        label={`${name}'s record vs expectation`}
        rows={seasons.map((r) => (
          <tr key={r.season}>
            <th scope="row">
              <Link href={hotSeatHref({ season: r.season })}>{r.season}</Link>
              <span className="block text-xs font-normal text-muted">
                {r.team}
                {r.throughWeek !== null ? `, through week ${r.throughWeek}` : ""}
              </span>
            </th>
            <td className="num whitespace-nowrap font-semibold">{r.record}</td>
            <td className="num">{r.expectedWins === null ? "not known" : r.expectedWins.toFixed(1)}</td>
            <td className="num font-semibold">{r.winsVsExpected === null ? "–" : signedNum(r.winsVsExpected)}</td>
          </tr>
        ))}
        table={(body) => (
          <div className="table-scroll">
            <table className="data-table" data-testid="coach-record-table">
              <caption className="sr-only">{name}&apos;s regular-season record and the market&apos;s expected wins, by season</caption>
              <thead>
                <tr>
                  <th scope="col">Season</th>
                  <th scope="col" className="num">Record</th>
                  <th scope="col" className="num">Market expected wins</th>
                  <th scope="col" className="num">Wins vs expectation</th>
                </tr>
              </thead>
              {body}
            </table>
          </div>
        )}
      />
    </section>
  );
}
