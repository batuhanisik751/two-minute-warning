import Term from "@/components/Term";
import { fmtInt, pct } from "@/lib/format";
import { historyRows, practiceLabel, type QHistoryRow } from "@/lib/questionable";

const TAG_TERM: Record<string, string> = { Questionable: "questionable", Doubtful: "doubtful" };

/** "How often tagged players play": per tag and practice status, the player-weeks and the share
 *  that took an offensive snap (questionable_history, published from the warehouse's rows). */
export default function HistoryTable({ rows }: { rows: QHistoryRow[] }) {
  const sorted = historyRows(rows);
  const seasons = sorted[0]?.seasons ?? "";
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid="q-history">
        <caption className="sr-only">How often QBs, RBs, WRs and TEs with each injury tag played, by practice status, seasons {seasons}</caption>
        <thead>
          <tr>
            <th scope="col">Tag</th>
            <th scope="col">
              <Term name="practice_status">Practice</Term>
            </th>
            <th scope="col" className="num">
              Player-weeks
            </th>
            <th scope="col" className="num">
              Played
            </th>
            <th scope="col" className="num">
              Share that played
            </th>
          </tr>
        </thead>
        {/* one body per tag (each a short group of rows: its total, then by practice) */}
        {[...new Set(sorted.map((r) => r.reportStatus))].map((tag) => (
          <tbody key={tag} data-status={tag}>
            {sorted
              .filter((r) => r.reportStatus === tag)
              .map((r) => (
                <tr key={`${r.reportStatus}-${r.practice}`} data-status={r.reportStatus} data-practice={r.practice} className={r.practice === "all" ? "font-semibold" : undefined}>
                  <th scope="row">{TAG_TERM[r.reportStatus] && r.practice === "all" ? <Term name={TAG_TERM[r.reportStatus]}>{r.reportStatus}</Term> : r.reportStatus}</th>
                  <td>{r.practice === "all" ? "All" : practiceLabel(r.practice)}</td>
                  <td className="num">{fmtInt(r.n)}</td>
                  <td className="num">{fmtInt(r.played)}</td>
                  <td className="num">{pct(r.playedRate)}</td>
                </tr>
              ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}
