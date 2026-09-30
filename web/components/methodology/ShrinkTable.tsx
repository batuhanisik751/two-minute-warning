import Term from "@/components/Term";
import { fmtInt } from "@/lib/format";
import { reliability, type ShrinkRow } from "@/lib/regression";

/** The frozen shrinkage table (model_versions.params.shrinkage.rows): per position, the
 *  split-half signal and noise of FPOE/game, the games for half weight, and the shrinkage
 *  factor r(g) after g games for the g given (the backtest's as-of weeks, from the data). */
export default function ShrinkTable({ rows, metric, games, caption }: { rows: ShrinkRow[]; metric: string; games: number[]; caption: string }) {
  const mine = rows.filter((r) => r.metric === metric);
  if (!mine.length) return null;
  return (
    <div className="table-scroll mt-3">
      <table className="data-table" data-testid={`shrink-${metric}`}>
        <caption className="text-left text-sm text-muted">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Position</th>
            <th scope="col" className="num">
              Player-seasons
            </th>
            <th scope="col" className="num">
              <Term name="signal_variance">Signal</Term>
            </th>
            <th scope="col" className="num">
              <Term name="noise_variance">Noise per game</Term>
            </th>
            <th scope="col" className="num">
              <Term name="games_for_half_weight">Games for half weight</Term>
            </th>
            {games.map((g) => (
              <th key={g} scope="col" className="num">
                r({g})
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {mine.map((r) => (
            <tr key={`${r.position}-${r.metric}`}>
              <th scope="row">{r.position}</th>
              <td className="num">{fmtInt(r.n)}</td>
              <td className="num">{r.signal.toFixed(2)}</td>
              <td className="num">{r.noise.toFixed(1)}</td>
              <td className="num">{r.halfWeight === null ? "–" : fmtInt(Math.round(r.halfWeight))}</td>
              {games.map((g) => (
                <td key={g} className="num">
                  {reliability(r, g).toFixed(2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
