import Link from "next/link";
import { FoldTable } from "@/components/Fold";
import Term from "@/components/Term";
import { KindBadge } from "@/components/ui";
import { fmtPoints } from "@/lib/format";
import type { RegressionHistoryRow } from "@/lib/queries/regression";
import { projectionText, regressionHref, signed, statsOf, tagTitle } from "@/lib/regression";

const val = (v: number | null, f: (x: number) => string) => (v === null ? "no data" : f(v));

/** A player's Regression Watch rows of one season: tag, numbers (with or without garbage
 *  time), projection and what he scored afterwards. */
export default function RegressionHistory({ rows, season, name, withGarbage }: { rows: RegressionHistoryRow[]; season: number; name: string; withGarbage: boolean }) {
  if (!rows.length) return <p className="mt-2 text-muted">He is not on any published Regression Watch list of {season}.</p>;
  // the 80% range (feature #4) beside the projection; lists without one show the projection alone
  const ranged = rows.some((r) => r.projectionLo !== null && r.projectionHi !== null);
  return (
    <FoldTable
      label={`Regression Watch lists with ${name}, ${season}`}
      table={(body) => (
        <div className="table-scroll mt-3">
          <table className="data-table" data-testid="regression-history">
            <caption className="sr-only">
              Regression Watch lists with {name}, {season} ({withGarbage ? "with" : "without"} garbage time)
            </caption>
            <thead>
              <tr>
                <th scope="col">Week</th>
                <th scope="col">List</th>
                <th scope="col">Tag</th>
                <th scope="col" className="num">
                  Games
                </th>
                <th scope="col" className="num">
                  PPG
                </th>
                <th scope="col" className="num">
                  <Term name="xfp">xFP</Term>/game
                </th>
                <th scope="col" className="num">
                  <Term name="fpoe">FPOE</Term>/game
                </th>
                <th scope="col" className="num">
                  <Term name="ppg_ros">Projection</Term>
                  {ranged ? (
                    <>
                      {" "}
                      (<Term name="projection_range">80% range</Term>)
                    </>
                  ) : null}
                </th>
                <th scope="col">
                  <Term name="rest_of_season_ppg">Rest of season</Term>
                </th>
              </tr>
            </thead>
            {body}
          </table>
        </div>
      )}
      rows={rows.map((r) => {
            const s = statsOf(r, withGarbage);
            const o = r.outcome;
            return (
              <tr key={`${r.week}-${r.kind}`}>
                <th scope="row">
                  <Link href={regressionHref({ season: r.season, week: r.week, kind: r.kind, withGarbage })}>Week {r.week}</Link>
                </th>
                <td>
                  <KindBadge kind={r.kind} />
                </td>
                <td>{r.tags.length ? r.tags.map(tagTitle).join(", ") : "No tag"}</td>
                <td className="num">{r.games}</td>
                <td className="num">{val(s.ppg, fmtPoints)}</td>
                <td className="num">{val(s.xfp, fmtPoints)}</td>
                <td className="num">{val(s.fpoe, (x) => signed(x))}</td>
                <td className="num whitespace-nowrap" data-testid="rw-history-projection">
                  {projectionText(r)}
                </td>
                <td className="tnum">
                  {o?.status === "final"
                    ? o.rosPpg !== null
                      ? `${fmtPoints(o.rosPpg)} PPG in ${o.rosGames ?? 0} ${o.rosGames === 1 ? "game" : "games"}`
                      : "no games"
                    : o
                      ? "pending"
                      : "not recorded"}
                </td>
              </tr>
            );
          })}
    />
  );
}
