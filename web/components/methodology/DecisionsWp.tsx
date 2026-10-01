import Term from "@/components/Term";
import { fmtInt, pct } from "@/lib/format";
import { TRACK_INTERVAL_LEVEL } from "@/lib/method";
import { smoothness, wpComparison, type Cell, type TrackRow } from "@/lib/decisions-track";

const NAMES: Record<string, string> = { own: "Our model", nflfastr_wp: "nflfastR wp", nflfastr_vegas_wp: "nflfastR vegas_wp" };
const SMOOTH: Record<string, string> = {
  score_step_h1: "Largest change for one point of score, first half",
  score_step_h2: "The same, third quarter to the start of the fourth",
  curvature: "Largest bend along a score line",
  halftime_possession: "What the ball is worth seconds before halftime, outside field-goal range",
  monotone_violations: "Times WP falls as the lead grows or the ball moves closer",
};

const f4 = (c: Cell | null) => (c ? c.value.toFixed(4) : "–");
const range4 = (c: Cell | null, signed = false) => {
  if (!c || c.lo === null || c.hi === null) return null;
  const s = (x: number) => `${signed && x > 0 ? "+" : ""}${x.toFixed(4)}`;
  return `${s(c.lo)} to ${s(c.hi)}`;
};
const one = (x: number | null) => (x === null ? "–" : x.toFixed(1));

/** Our WP model against nflfastR (pooled test seasons) and its smoothness limits. */
export default function DecisionsWp({ rows }: { rows: TrackRow[] }) {
  const c = wpComparison(rows);
  const s = smoothness(rows);
  if (!c) return <p className="mt-3 text-muted">The WP model&apos;s backtest has not been published.</p>;
  return (
    <>
      <p className="mt-3">
        <Term name="own_wp">Our win-probability model</Term> is trained walk-forward: each test season {c.span.replace("-", "–")}{" "}
        is scored by a model that learned only from earlier seasons. On {c.n !== null ? fmtInt(c.n) : "the"} test plays it is
        compared with nflfastR&apos;s two public models (partly in-sample: they were fit on many seasons, including these).
        Lower Brier and log loss are better; the calibration error is the average gap, in percentage points, between
        forecasts and how often teams really won.
      </p>
      <div className="table-scroll mt-3">
        <table className="data-table" data-testid="wp-table">
          <caption className="text-left text-sm text-muted">
            Win probability, {c.span.replace("-", "–")} pooled ({pct(TRACK_INTERVAL_LEVEL)} season-block intervals)
          </caption>
          <thead>
            <tr>
              <th scope="col">Method</th>
              <th scope="col" className="num">Brier</th>
              <th scope="col" className="num">Log loss</th>
              <th scope="col" className="num">Calibration error (points)</th>
            </tr>
          </thead>
          <tbody>
            {c.methods.map((m) => (
              <tr key={m.method}>
                <th scope="row">{NAMES[m.method] ?? m.method}</th>
                <td className="num">{f4(m.brier)}<span className="block text-xs text-muted">{range4(m.brier)}</span></td>
                <td className="num">{f4(m.logLoss)}<span className="block text-xs text-muted">{range4(m.logLoss)}</span></td>
                <td className="num">{m.ece ? (m.ece.value * 100).toFixed(1) : "–"}</td>
              </tr>
            ))}
            {c.diffs.map((d) => (
              <tr key={d.method}>
                <th scope="row">Ours minus {NAMES[d.method]}</th>
                <td className="num">{d.brier ? `${d.brier.value > 0 ? "+" : ""}${f4(d.brier)}` : "–"}<span className="block text-xs text-muted">{range4(d.brier, true)}</span></td>
                <td className="num">{d.logLoss ? `${d.logLoss.value > 0 ? "+" : ""}${f4(d.logLoss)}` : "–"}<span className="block text-xs text-muted">{range4(d.logLoss, true)}</span></td>
                <td className="num">–</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <SmoothnessTable s={s} />
    </>
  );
}

function SmoothnessTable({ s }: { s: ReturnType<typeof smoothness> }) {
  if (!s.metrics.length) return null;
  return (
    <>
      <p className="mt-4">
        A grade is a difference between the win probabilities of situations that did not happen, so the model must also be
        smooth: one point of score or a few seconds should not swing it. Limits were fixed on a grid of made-up situations{" "}
        <em>before</em> any fix was tried, and every candidate model was judged on the validation seasons
        {s.validation ? ` ${s.validation.replace("-", "–")}` : ""} only. The model in use is a smooth logistic spline of the
        lead in standard deviations of what can still happen (the ball&apos;s value before halftime included), with no
        possession advantage left as the half runs out; from the start of the fourth quarter it hands over, in steps, to
        one monotone gradient-boosted model, where real score thresholds (a tie, a field goal, a touchdown) matter. The
        limits stop at the start of the fourth quarter by design: <strong>the fourth quarter is not covered by them</strong>,
        and that is where our grades and nfl4th&apos;s differ most (below).
      </p>
      <div className="table-scroll mt-3">
        <table className="data-table" data-testid="smoothness-table">
          <caption className="text-left text-sm text-muted">
            Smoothness in WP points (0-100): the limit, the worst test fold of the model in use
            {s.folds ? ` (${s.folds.replace("-", "–")})` : ""} and of the first model before the fix, and nflfastR&apos;s
            vegas_wp for reference
          </caption>
          <thead>
            <tr>
              <th scope="col">Check</th>
              <th scope="col" className="num">Limit</th>
              <th scope="col" className="num">Ours (worst fold)</th>
              <th scope="col" className="num">Before the fix</th>
              <th scope="col" className="num">nflfastR vegas_wp</th>
            </tr>
          </thead>
          <tbody>
            {s.metrics.map((m) => (
              <tr key={m.metric}>
                <th scope="row">{SMOOTH[m.metric] ?? m.metric}</th>
                <td className="num">{one(m.limit)}</td>
                <td className="num">{one(m.ours)}</td>
                <td className="num">{one(m.before)}</td>
                <td className="num">{one(m.vegas)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
