import { band, BAND_LABEL, fmtRating, opponentLabel, type GridCell } from "@/lib/playoff-planner";

const BAND_CLASS = {
  easy: "border-hit text-hit font-semibold",
  neutral: "border-line-strong text-muted",
  hard: "border-line-strong text-miss font-semibold",
} as const;

/** One week of the grid: the opponent ("vs KC", "@ KC", "Bye") and, at a rated position, the
 *  matchup rating with its word (easy / neutral / hard) and its rank among the opponents. */
export default function RatingCell({ cell, rated }: { cell: GridCell; rated: boolean }) {
  const b = cell.rating !== null ? band(cell.rating) : null;
  return (
    <td data-week={cell.week} data-cell={`w${cell.week}`} data-band={b ?? (cell.opponent ? "unrated" : "bye")} className="whitespace-nowrap">
      <span className={cell.opponent ? "font-medium" : "text-muted"}>{opponentLabel(cell)}</span>
      {rated && cell.rating !== null && b ? (
        <span className="mt-0.5 flex items-center gap-1.5 text-xs">
          <span className={`inline-flex rounded border-[1.5px] px-1.5 py-0.5 ${BAND_CLASS[b]}`}>
            {fmtRating(cell.rating)} {BAND_LABEL[b]}
          </span>
          {cell.rank !== null ? <span className="text-muted">rank {cell.rank}</span> : null}
        </span>
      ) : null}
    </td>
  );
}
