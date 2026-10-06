import Term from "@/components/Term";
import { NotPublished } from "@/components/track/parts";
import { fmtInt } from "@/lib/format";
import { candidatesOf, PP_POSITIONS, verdict, type PPBacktestRow, type PPParams } from "@/lib/playoff-planner";
import { positionShort } from "@/lib/positions";

/** The four candidates per position (playoff_planner_backtest): the points MAE pooled over the
 *  four as-of weeks and the test seasons, the seasons each beat the choice it was compared with
 *  and the fixed rule's verdict; the rule's pick is marked, and an owner override would be
 *  disclosed (chosen_by). */
export default function Candidates({ rows, params }: { rows: PPBacktestRow[]; params: PPParams | null }) {
  if (!rows.length) return <NotPublished what="The candidates' backtest">It appears with the first publish of the playoff planner.</NotPublished>;
  const needed = params?.season_wins_needed;
  const by = params?.chosen_by;
  return (
    <div className="space-y-3" data-testid="pp-candidates">
      <p className="max-w-3xl text-sm">
        {params?.rule ? `The rule, fixed before any test number was seen: ${params.rule}.` : null}{" "}
        {by && by !== "rule" ? `The site overrides it: ${by}.` : "The site uses the rule's pick at every position."}
      </p>
      <div className="table-scroll">
        <table className="data-table" data-testid="pp-candidates-table">
          <caption className="sr-only">Each position&apos;s four candidates: walk-forward points error and the rule&apos;s verdict</caption>
          <thead>
            <tr>
              <th scope="col">Position</th>
              <th scope="col">Candidate</th>
              <th scope="col" className="num">
                <Term name="mae">MAE</Term> (points)
              </th>
              <th scope="col" className="num">Player-games</th>
              <th scope="col">The rule</th>
            </tr>
          </thead>
          {PP_POSITIONS.map((p) => (
            <tbody key={p} data-pos={p}>
              {candidatesOf(rows, p).map((r) => (
                <tr key={`${p}-${r.candidate}`} data-pos={p} data-candidate={r.candidate} data-pick={r.rulePick ? "yes" : undefined}>
                  <th scope="row">{positionShort(p)}</th>
                  <td className={r.chosen ? "font-semibold" : undefined}>
                    {r.title}
                    {r.rulePick ? <span className="ml-1.5 text-xs font-semibold text-hit">the rule&apos;s pick</span> : null}
                  </td>
                  <td className="num">{r.mae.toFixed(3)}</td>
                  <td className="num">{fmtInt(r.n)}</td>
                  <td className="text-sm">{verdict(r, needed)}</td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </div>
    </div>
  );
}
