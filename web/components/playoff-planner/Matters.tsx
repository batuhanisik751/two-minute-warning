import Term from "@/components/Term";
import { NotPublished } from "@/components/track/parts";
import { pct } from "@/lib/format";
import { isRated, missRange, pooledEffects, stabilityRows, type PPBacktestRow, type PPChoice, type PPEffect, type PPStability } from "@/lib/playoff-planner";
import { positionShort } from "@/lib/positions";

const gap = (x: number | null) => (x === null ? "–" : x.toFixed(1));
const rho = (x: number | null) => (x === null ? "–" : x.toFixed(2));

/** "How much do matchups matter?": per position, the gap between the easiest and the hardest
 *  fifth of matchups that the raw ratings implied, the gap the shrunk ratings (the ones the site
 *  builds on) implied, and the gap that really showed up in weeks 15-17 (playoff_planner_effects,
 *  horizon 'all'), whether the site rates it, and the stability (playoff_planner_stability). */
export default function Matters({ effects, stability, choice, backtest }: { effects: PPEffect[]; stability: PPStability[]; choice: PPChoice[]; backtest: PPBacktestRow[] }) {
  const rows = pooledEffects(effects);
  if (!rows.length) return <NotPublished what="The effect sizes">They appear with the first publish of the playoff planner.</NotPublished>;
  const miss = missRange(backtest);
  const used = new Map(choice.map((c) => [c.position, c.candidate]));
  const stab = stabilityRows(stability);
  const [early, late] = stab.length ? [stab[0].early.horizon, stab[0].late.horizon] : [null, null];
  return (
    <div className="space-y-6">
      <div className="table-scroll">
        <table className="data-table" data-testid="pp-effects">
          <caption className="sr-only">How much a matchup mattered in fantasy weeks 15-17: the easiest fifth of matchups vs the hardest fifth, points per game</caption>
          <thead>
            <tr>
              <th scope="col">Position</th>
              <th scope="col" className="num">Raw ratings said</th>
              <th scope="col" className="num">Shrunk ratings said</th>
              <th scope="col" className="num">
                Really happened (<Term name="matchup_gap">gap</Term>)
              </th>
              <th scope="col" className="num">Share of the raw gap that held up</th>
              <th scope="col">On this site</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.position} data-pos={e.position}>
                <th scope="row">{positionShort(e.position)}</th>
                <td className="num">{gap(e.ratedGap)}</td>
                <td className="num">{gap(e.shrunkGap)}</td>
                <td className="num font-semibold">{gap(e.realizedGap)}</td>
                <td className="num">{e.survived === null ? "–" : pct(e.survived)}</td>
                <td>{isRated(used.get(e.position)) ? "rated" : "not rated: every matchup 1.00"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="max-w-3xl text-sm text-muted">
        Points per game in weeks 15-17, players facing the easiest fifth of opponents minus those facing the hardest fifth, measured against each player&apos;s own usual
        points. The raw ratings promise far more than really happened; the shrunk ratings, pulled toward average the way the site&apos;s ratings are, come much
        closer.{" "}
        {miss ? `A player's points usually miss his own average by ${miss[0].toFixed(1)}-${miss[1].toFixed(1)} points a week (QB, RB, WR), so these gaps are real but small next to that.` : ""}
      </p>
      {stab.length ? (
        <>
          <div className="table-scroll">
            <table className="data-table" data-testid="pp-stability">
              <caption className="sr-only">How well a team&apos;s rating early in the season matched its rating in weeks 15-17 (rank correlation, 1 = perfectly)</caption>
              <thead>
                <tr>
                  <th scope="col">Position</th>
                  <th scope="col" className="num">As of week {early}</th>
                  <th scope="col" className="num">As of week {late}</th>
                </tr>
              </thead>
              <tbody>
                {stab.map((s) => (
                  <tr key={s.position} data-pos={s.position}>
                    <th scope="row">{positionShort(s.position)}</th>
                    <td className="num">{rho(s.early.rhoMean)}</td>
                    <td className="num">{rho(s.late.rhoMean)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="max-w-3xl text-sm text-muted" data-testid="pp-stability-note">
            Stability: how well the raw ratings as of week {early}, and as of week {late}, put the teams in the order of the points they really allowed in weeks 15-17,
            as a rank correlation averaged over {stab[0].late.seasons} seasons (1 = the same order, 0 = no relation at all).
          </p>
        </>
      ) : null}
    </div>
  );
}
