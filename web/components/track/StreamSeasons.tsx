import { FoldTable } from "@/components/Fold";
import { fmtInt, pct } from "@/lib/format";
import { positionLabel } from "@/lib/positions";
import { streamMethodName, type StreamTrackRow } from "@/lib/streamer";
import type { StreamSeason } from "@/lib/track-record";

const rate = (r: StreamTrackRow | null) => (r && r.value !== null ? pct(r.value, 1) : "–");

/** The streamer's per-season results of one position: the method on the site against its
 *  rival, the weekly lists, the pool picks and how often a random pool pick had a starter week. */
export default function StreamSeasons({ rows, position, ours, other, topN, starter }: { rows: StreamSeason[]; position: string; ours: string; other: string; topN: number; starter: string }) {
  const label = `The ${positionLabel(position)} backtest, season by season`;
  return (
    <FoldTable
      label={label}
      rows={rows.map((r) => {
        const any = r.ours ?? r.other;
        const base = any && any.nRows && any.nPos !== null ? any.nPos / any.nRows : null;
        return (
          <tr data-row="" key={r.season}>
            <th scope="row" data-cell="season">{r.season}</th>
            <td data-cell="value" className="num">{rate(r.ours)}</td>
            <td data-cell="value" className="num">{rate(r.other)}</td>
            <td data-cell="value" className="num">{base !== null ? pct(base, 1) : "–"}</td>
            <td data-cell="value" className="num">{any?.nGroups != null ? fmtInt(any.nGroups) : "–"}</td>
            <td data-cell="value" className="num">{any?.nRows != null ? fmtInt(any.nRows) : "–"}</td>
          </tr>
        );
      })}
      table={(body) => (
        <div className="table-scroll">
          <table className="data-table" data-testid={`stream-seasons-${position}`}>
            <caption className="text-left text-sm text-muted">
              {positionLabel(position)}: share of the top {topN} with a {starter} the next week, newest season first
            </caption>
            <thead>
              <tr>
                <th scope="col">Season</th>
                <th scope="col" className="num">{streamMethodName(ours, position)} (on the site)</th>
                <th scope="col" className="num">{streamMethodName(other, position)}</th>
                <th scope="col" className="num">A random pool pick</th>
                <th scope="col" className="num">Lists</th>
                <th scope="col" className="num">Pool picks</th>
              </tr>
            </thead>
            {body}
          </table>
        </div>
      )}
    />
  );
}
