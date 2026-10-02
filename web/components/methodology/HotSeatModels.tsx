import { fmtInt, pct } from "@/lib/format";
import { MODEL_WORDS, trackCell, type TrackCell, type TrackCellRow } from "@/lib/hot-seat";
import { TRACK_INTERVAL_LEVEL } from "@/lib/method";

const COLUMNS = [
  { slice: "all", metric: "roc_auc", label: "ROC-AUC, every row", digits: 3 },
  { slice: "all", metric: "pr_auc", label: "PR-AUC, every row", digits: 3 },
  { slice: "all", metric: "brier", label: "Brier, every row (lower is better)", digits: 4 },
  { slice: "end_of_season", metric: "top5_hit_rate", label: "Top-5 hit rate, end of season", digits: 3 },
] as const;

function Cell({ c, digits }: { c: TrackCell | null; digits: number }) {
  if (!c) return <td className="num text-muted">–</td>;
  return (
    <td className="num">
      <span className="font-semibold">{c.value.toFixed(digits)}</span>
      {c.lo !== null && c.hi !== null ? (
        <span className="block text-xs text-muted">
          {c.lo.toFixed(digits)} to {c.hi.toFixed(digits)}
        </span>
      ) : null}
    </td>
  );
}

/** The walk-forward comparison of every model the backtest ran (main run, the models' own
 *  probabilities), with the season-block intervals: hot_seat_track_record, row for row. */
export default function HotSeatModels({ rows }: { rows: TrackCellRow[] }) {
  const models = Object.keys(MODEL_WORDS).filter((m) => rows.some((r) => r.variant === "main" && r.model === m && r.prob === "prob"));
  if (!models.length) return null;
  const n = trackCell(rows, { model: "logit", slice: "all", metric: "roc_auc" });
  return (
    <div className="space-y-2">
      <div className="table-scroll">
        <table className="data-table" data-testid="hot-seat-models">
          <caption className="sr-only">The Hot-Seat models in the walk-forward backtest, with {pct(TRACK_INTERVAL_LEVEL)} intervals</caption>
          <thead>
            <tr>
              <th scope="col">Model</th>
              {COLUMNS.map((c) => (
                <th key={`${c.slice}-${c.metric}`} scope="col" className="num">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m} data-model={m}>
                <th scope="row">{MODEL_WORDS[m]}</th>
                {COLUMNS.map((c) => (
                  <Cell key={`${c.slice}-${c.metric}`} c={trackCell(rows, { model: m, slice: c.slice, metric: c.metric })} digits={c.digits} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-muted">
        Under each number, its {pct(TRACK_INTERVAL_LEVEL)} interval from redrawing whole seasons.
        {n ? ` ${fmtInt(n.nRows)} coach-weeks and snapshots (${fmtInt(n.nPos)} of them for coaches who were then let go), ${fmtInt(n.nSeasons)} test seasons, interim coaches left out.` : ""}{" "}
        The top-5 hit rate is the share of the coaches let go who were in their season&apos;s top 5 at that point.
      </p>
    </div>
  );
}
