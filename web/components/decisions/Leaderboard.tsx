import Link from "next/link";
import { FoldTable } from "@/components/Fold";
import Term from "@/components/Term";
import { aggressivenessWords, coachTotals, wpPoints } from "@/lib/decisions";
import { fmtInt } from "@/lib/format";
import type { CoachSeasonRow } from "@/lib/queries/decisions";

/** The column headers shared by the league leaderboard and a coach's seasons table. */
export function CountHeaders() {
  return (
    <>
      <th scope="col" className="num">
        Games
      </th>
      <th scope="col" className="num">
        <Term name="decisions_graded">Decisions</Term>
      </th>
      <th scope="col" className="num">
        <Term name="clear_call">Clear calls</Term>
      </th>
      <th scope="col" className="num">
        <Term name="wrong_call">Wrong</Term>
      </th>
      <th scope="col" className="num">
        <Term name="wp_lost_per_game">WP lost per game</Term>
      </th>
      <th scope="col">
        <Term name="aggressiveness">Aggressiveness</Term>
      </th>
      <th scope="col" className="num">
        <Term name="clock_case">Clock cases</Term>
      </th>
    </>
  );
}

/** The cells under CountHeaders, from one coach_season row. */
export function CountCells({ r }: { r: CoachSeasonRow }) {
  const t = coachTotals(r);
  return (
    <>
      <td className="num">{fmtInt(r.games)}</td>
      <td className="num">{fmtInt(t.decisions)}</td>
      <td className="num">{fmtInt(t.clear)}</td>
      <td className="num">{fmtInt(t.wrong)}</td>
      <td className="num" data-cell-kind="wp-lost-per-game">
        {wpPoints(r.wpLostPerGame, 2)}
      </td>
      <td>{aggressivenessWords(r.goClearWent, r.goClear)}</td>
      <td className="num">{fmtInt(t.clockCases)}</td>
    </>
  );
}

/** The league leaderboard of one season: the ranked coaches (least WP lost per game first),
 *  folded after 10 rows. */
export default function Leaderboard({ rows, season, minGames }: { rows: CoachSeasonRow[]; season: number; minGames: number }) {
  const label = `Coach leaderboard, ${season}`;
  return (
    <FoldTable
      label={label}
      rows={rows.map((r, i) => (
        <tr key={r.coachId} data-coach={r.coachId}>
          <td className="num">{i + 1}</td>
          <th scope="row">
            <Link href={`/coach/${r.coachId}`}>{r.name}</Link> <span className="font-normal text-muted">{r.team}</span>
          </th>
          <CountCells r={r} />
        </tr>
      ))}
      table={(body) => (
        <>
          {/* the sentence sits above the scrolling table, so a narrow screen shows all of it */}
          <p className="mb-2 text-sm font-medium text-muted" data-testid="leaderboard-rule">
            Coaches with at least {minGames} games, least WP lost per game first (WP points: 1 = one percentage point of win
            probability).
          </p>
          <div className="table-scroll">
            <table className="data-table" data-testid="leaderboard">
              <caption className="sr-only">{label}</caption>
              <thead>
                <tr>
                  <th scope="col" className="num">
                    Rank
                  </th>
                  <th scope="col">Coach</th>
                  <CountHeaders />
                </tr>
              </thead>
              {body}
            </table>
          </div>
        </>
      )}
    />
  );
}
