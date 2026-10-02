import { FoldTable } from "@/components/Fold";
import { fmtInt } from "@/lib/format";
import type { HotSeatFiringsRow } from "@/lib/queries/hot-seat";

/** Head-coach departures per season (hot_seat_firings, coach-seasons; interims apart), newest
 *  first; seasons before the first test season were used for training only. */
export default function HotSeatFirings({ rows, firstTest }: { rows: HotSeatFiringsRow[]; firstTest: number | null }) {
  if (!rows.length) return null;
  return (
    <FoldTable
      label="Head-coach departures per season"
      rows={rows.map((r) => (
        <tr key={r.season}>
          <th scope="row">
            {r.season}
            {firstTest !== null && r.season < firstTest ? <span className="block text-xs font-normal text-muted">training only</span> : null}
          </th>
          <td className="num">{fmtInt(r.positiveDepartures)}</td>
          <td className="num">{fmtInt(r.firedInSeason)}</td>
          <td className="num">{fmtInt(r.positivesWeek12)}</td>
          <td className="num">{fmtInt(r.positivesEndOfSeason)}</td>
          <td className="num">{fmtInt(r.censoredCoachSeasons)}</td>
          <td className="num">{fmtInt(r.interimCoachSeasons)}</td>
        </tr>
      ))}
      table={(body) => (
        <div className="table-scroll">
          <table className="data-table" data-testid="hot-seat-firings">
            <caption className="sr-only">Head-coach departures per season</caption>
            <thead>
              <tr>
                <th scope="col">Season</th>
                <th scope="col" className="num">Let go</th>
                <th scope="col" className="num">Of them during the season</th>
                <th scope="col" className="num">Let go, still coaching at week 12</th>
                <th scope="col" className="num">Let go, coached to season end</th>
                <th scope="col" className="num">Left another way</th>
                <th scope="col" className="num">Interim coaches</th>
              </tr>
            </thead>
            {body}
          </table>
        </div>
      )}
    />
  );
}
