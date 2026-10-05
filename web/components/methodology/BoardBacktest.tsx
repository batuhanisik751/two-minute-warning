import Term from "@/components/Term";
import { boardCell, diffWords, type BoardCell, type BoardTrackRow } from "@/lib/board";
import { signedNum } from "@/lib/hot-seat";
import { pct } from "@/lib/format";
import { TRACK_INTERVAL_LEVEL } from "@/lib/method";

/** The variants of board_track_record the site describes, with their primary model. */
export const BOARD_VARIANTS = {
  cliff_main: { model: "logit", name: "Chance of a Cliff (shown)" },
  cliff_missed: { model: "logit_simple", name: "Chance of missed time (shown)" },
  cliff_sensitivity: { model: "logit", name: "Either one (a check: missed time counted as a Cliff)" },
  breakout_wr_te: { model: "logit", name: "Breakout, wide receivers and tight ends (research)" },
  breakout_rb: { model: "logit", name: "Breakout, running backs (research)" },
} as const;
export type BoardVariant = keyof typeof BOARD_VARIANTS;

function Cell({ c, diff = false }: { c: BoardCell | null; diff?: boolean }) {
  if (!c) return <td className="num text-muted">–</td>;
  const f = (x: number) => (diff ? signedNum(x, 3) : x.toFixed(3));
  return (
    <td className="num" data-words={diff ? diffWords(c) : undefined}>
      <span className="font-semibold">{f(c.value)}</span>
      {c.lo !== null && c.hi !== null ? (
        <span className="block text-xs text-muted">
          {f(c.lo)} to {f(c.hi)}
        </span>
      ) : null}
      {diff ? <span className="block text-xs text-muted">{diffWords(c)}</span> : null}
    </td>
  );
}

/** PR-AUC of each variant's primary model over every reconstructed board, its paired difference
 *  against last season's PPG rank (and, for the shown chances, against the same model at the end
 *  of the season), and in the experts' era its difference against the experts' ranking. */
export default function BoardBacktest({ rows, variants, testId, first = "Chance" }: { rows: BoardTrackRow[]; variants: BoardVariant[]; testId: string; first?: string }) {
  const shown = variants.filter((v) => rows.some((r) => r.variant === v));
  if (!shown.length) return null;
  const withEos = shown.some((v) => boardCell(rows, { variant: v, slice: "all", model: BOARD_VARIANTS[v].model, metric: "pr_auc_diff", vs: "eos" }));
  return (
    <div className="table-scroll">
      <table className="data-table" data-testid={testId}>
        <caption className="sr-only">PR-AUC in the walk-forward backtest with {pct(TRACK_INTERVAL_LEVEL)} intervals and paired differences</caption>
        <thead>
          <tr>
            <th scope="col">{first}</th>
            <th scope="col" className="num">
              <Term name="pr_auc">PR-AUC</Term>, every board
            </th>
            <th scope="col" className="num">
              Against last season&apos;s <Term name="ppg">PPG</Term> rank
            </th>
            {withEos ? <th scope="col" className="num">Against the end-of-season snapshot</th> : null}
            <th scope="col" className="num">Experts&apos; era: against the experts</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((v) => {
            const m = BOARD_VARIANTS[v].model;
            const q = (slice: string, metric: string, vs: string | null = null) => boardCell(rows, { variant: v, slice, model: m, metric, vs });
            return (
              <tr key={v} data-variant={v}>
                <th scope="row">{BOARD_VARIANTS[v].name}</th>
                <Cell c={q("all", "pr_auc")} />
                <Cell c={q("all", "pr_auc_diff", "base_ppg_rank")} diff />
                {withEos ? <Cell c={q("all", "pr_auc_diff", "eos")} diff /> : null}
                <Cell c={q("ecr_era", "pr_auc_diff", "ecr")} diff />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
