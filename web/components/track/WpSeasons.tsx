import { FoldTable } from "@/components/Fold";
import Term from "@/components/Term";
import { fmtInt, pct } from "@/lib/format";
import { TRACK_INTERVAL_LEVEL } from "@/lib/method";
import type { WpSeason } from "@/lib/track-record";

type Num = WpSeason["brier"];
const f4 = (x: number, signed = false) => `${signed && x > 0 ? "+" : ""}${x.toFixed(4)}`;

function Diff({ d }: { d: Num }) {
  if (!d) return <>–</>;
  return (
    <>
      {f4(d.value, true)}
      {d.lo !== null && d.hi !== null ? (
        <span className="block text-xs whitespace-nowrap text-muted">
          {f4(d.lo, true)} to {f4(d.hi, true)}
        </span>
      ) : null}
    </>
  );
}

/** The WP model's test seasons, newest first: plays, our Brier and log loss, and our log loss
 *  minus nflfastR's two models' (with their intervals; lower is better). */
export default function WpSeasons({ rows }: { rows: WpSeason[] }) {
  const label = "Our win-probability model, season by season";
  return (
    <FoldTable
      label={label}
      rows={rows.map((r) => (
        <tr data-row="" key={r.season}>
          <th scope="row" data-cell="season">{r.season}</th>
          <td data-cell="value" className="num">{r.n !== null ? fmtInt(r.n) : "–"}</td>
          <td data-cell="value" className="num">{r.brier ? f4(r.brier.value) : "–"}</td>
          <td data-cell="value" className="num">{r.logLoss ? f4(r.logLoss.value) : "–"}</td>
          <td data-cell="value" className="num">
            <Diff d={r.vsWp} />
          </td>
          <td data-cell="value" className="num">
            <Diff d={r.vsVegas} />
          </td>
        </tr>
      ))}
      table={(body) => (
        <div className="table-scroll">
          <table className="data-table" data-testid="wp-seasons">
            <caption className="text-left text-sm text-muted">
              {label}, newest first (<Term name="log_loss">log loss</Term>: lower is better; {pct(TRACK_INTERVAL_LEVEL)} intervals under the
              differences)
            </caption>
            <thead>
              <tr>
                <th scope="col">Season</th>
                <th scope="col" className="num">Plays</th>
                <th scope="col" className="num">
                  <Term name="brier">Brier</Term>
                </th>
                <th scope="col" className="num">
                  <Term name="log_loss">Log loss</Term>
                </th>
                <th scope="col" className="num">Minus nflfastR wp</th>
                <th scope="col" className="num">Minus nflfastR vegas_wp</th>
              </tr>
            </thead>
            {body}
          </table>
        </div>
      )}
    />
  );
}
