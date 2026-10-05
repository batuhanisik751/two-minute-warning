import { FoldTable } from "@/components/Fold";
import Term from "@/components/Term";
import { fmtInt } from "@/lib/format";
import type { RegressionTrackRow } from "@/lib/regression";

/** What each Regression Watch test season used, chosen walk-forward on the seasons before it
 *  (regression_track_record `choice` and `threshold` rows): the version, its error on those
 *  earlier seasons and the tag cutoffs. These are not the season's own test results. */
export default function RegressionChoices({ rows }: { rows: RegressionTrackRow[] }) {
  const choices = rows.filter((r) => r.section === "choice" && r.season !== null).sort((a, b) => (b.season ?? 0) - (a.season ?? 0));
  if (!choices.length) return null;
  const cut = (season: number, tag: string) => rows.find((r) => r.section === "threshold" && r.season === season && r.metric === tag && r.rowGroup === "chosen");
  const label = "What each Regression Watch test season used";
  return (
    <FoldTable
      label={label}
      rows={choices.map((c) => {
        const sell = cut(c.season as number, "sell_high");
        const buy = cut(c.season as number, "buy_low");
        return (
          <tr data-row="" key={c.season}>
            <th scope="row" data-cell="season">{c.season}</th>
            <td data-cell="value">
              <span className="font-mono text-xs">{c.method ?? "–"}</span>
            </td>
            <td data-cell="value" className="num">{c.value.toFixed(2)}</td>
            <td data-cell="value" className="num">{fmtInt(c.n)}</td>
            <td data-cell="value" className="num">{sell ? sell.value.toFixed(1) : "–"}</td>
            <td data-cell="value" className="num">{buy ? buy.value.toFixed(1) : "–"}</td>
          </tr>
        );
      })}
      table={(body) => (
        <div className="table-scroll">
          <table className="data-table" data-testid="rw-choices">
            <caption className="text-left text-sm text-muted">
              {label}, newest first: the version and cutoffs chosen on the earlier seasons only, and the version&apos;s error
              there (points per game)
            </caption>
            <thead>
              <tr>
                <th scope="col">Season</th>
                <th scope="col">Version</th>
                <th scope="col" className="num">
                  <Term name="mae">Error</Term> on earlier seasons
                </th>
                <th scope="col" className="num">Player-weeks</th>
                <th scope="col" className="num">Sell-high cutoff</th>
                <th scope="col" className="num">Buy-low cutoff</th>
              </tr>
            </thead>
            {body}
          </table>
        </div>
      )}
    />
  );
}
