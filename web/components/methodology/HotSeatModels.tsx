import Term from "@/components/Term";
import { fmtInt, pct } from "@/lib/format";
import { MODEL_WORDS, top5Slices, trackCell, type TrackCell, type TrackCellRow } from "@/lib/hot-seat";
import { TRACK_INTERVAL_LEVEL } from "@/lib/method";

// `term`: the metric's name, a glossary term (the entry is named like the metric); `label` follows it
// `share`: a share of the coaches let go, shown as a percentage like the tiles (lib/hot-seat.ts headlineStats)
const COLUMNS: { slice: string; metric: string; term?: string; label: string; digits: number; share?: boolean }[] = [
  { slice: "all", metric: "roc_auc", term: "ROC-AUC", label: ", every row", digits: 3 },
  { slice: "all", metric: "pr_auc", term: "PR-AUC", label: ", every row", digits: 3 },
  { slice: "all", metric: "brier", term: "Brier", label: ", every row (lower is better)", digits: 4 },
];

/** The table's columns: the every-row metrics, then the top-5 hit rate at each published point
 *  (week 12 beside the season's end, lib/hot-seat.ts top5Slices). */
const columns = (rows: TrackCellRow[]): typeof COLUMNS => [
  ...COLUMNS,
  ...top5Slices(rows).map((t) => ({ slice: t.slice, metric: "top5_hit_rate", label: `Top-5 hit rate, ${t.when}`, digits: 0, share: true })),
];

function Cell({ c, digits, share = false }: { c: TrackCell | null; digits: number; share?: boolean }) {
  if (!c) return <td className="num text-muted">–</td>;
  const f = (x: number) => (share ? pct(x, digits) : x.toFixed(digits));
  return (
    <td className="num">
      <span className="font-semibold">{f(c.value)}</span>
      {c.lo !== null && c.hi !== null ? (
        <span className="block text-xs text-muted">
          {f(c.lo)} to {f(c.hi)}
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
  const cols = columns(rows);
  return (
    <div className="space-y-2">
      <div className="table-scroll">
        <table className="data-table" data-testid="hot-seat-models">
          <caption className="sr-only">The Hot-Seat models in the walk-forward backtest, with {pct(TRACK_INTERVAL_LEVEL)} intervals</caption>
          <thead>
            <tr>
              <th scope="col">Model</th>
              {cols.map((c) => (
                <th key={`${c.slice}-${c.metric}`} scope="col" className="num">
                  {c.term ? <Term name={c.metric}>{c.term}</Term> : null}
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m} data-model={m}>
                <th scope="row">{MODEL_WORDS[m]}</th>
                {cols.map((c) => (
                  <Cell key={`${c.slice}-${c.metric}`} c={trackCell(rows, { model: m, slice: c.slice, metric: c.metric })} digits={c.digits} share={c.share} />
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
