import Term from "@/components/Term";
import CalibrationFigure from "@/components/track/CalibrationFigure";
import { NotPublished, StatTiles } from "@/components/track/parts";
import { fmtInt, pct } from "@/lib/format";
import { backtestScore, calibrationPoints, liveRecord, type QBacktestRow, type QCalibrationRow, type QLiveRow } from "@/lib/questionable";

// The Questionable record's pieces, shared by /questionable and /track-record: the walk-forward
// score against the baseline, the calibration (plot and table) and the live record. Every number
// is a published row (questionable_backtest, questionable_calibration, questionable_live).

/** Pooled log loss and Brier score of the chosen grouping and of the baseline. */
export function BacktestScore({ rows }: { rows: QBacktestRow[] }) {
  const s = backtestScore(rows);
  if (!s) return <NotPublished what="The walk-forward backtest">It appears with the first publish of the Questionable table.</NotPublished>;
  const better = s.chosen.logLoss < s.baseline.logLoss && s.chosen.brier < s.baseline.brier;
  return (
    <div className="space-y-3" data-testid="q-backtest">
      <p>
        Walk-forward over {s.seasons.length} seasons ({s.seasons[0]}-{s.seasons[s.seasons.length - 1]}, {fmtInt(s.chosen.n)} tagged players): each season scored by a table built
        only from the seasons before it. Lower is better for both scores.
      </p>
      <div className="table-scroll">
        <table className="data-table" data-testid="q-backtest-table">
          <caption className="sr-only">Pooled walk-forward scores: the chance shown against the baseline</caption>
          <thead>
            <tr>
              <th scope="col">Chance</th>
              <th scope="col" className="num">
                <Term name="log_loss">Log loss</Term>
              </th>
              <th scope="col" className="num">
                <Term name="brier">Brier score</Term>
              </th>
            </tr>
          </thead>
          <tbody>
            {[s.chosen, s.baseline].map((r) => (
              <tr key={r.grouping} data-grouping={r.grouping}>
                <th scope="row">{r.chosen ? `Shown: ${r.title}` : `${r.title.charAt(0).toUpperCase()}${r.title.slice(1)}`}</th>
                <td className="num">{r.logLoss.toFixed(4)}</td>
                <td className="num">{r.brier.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-muted">{better ? "The chance shown beats the baseline on both scores." : "The chance shown does not beat the baseline on both scores."}</p>
    </div>
  );
}

/** Predicted against actual by chance bucket, with the rows in each. */
export function QCalibration({ rows, id }: { rows: QCalibrationRow[]; id: string }) {
  const points = calibrationPoints(rows);
  if (!points.length) return <NotPublished what="The calibration">It appears with the first publish of the Questionable table.</NotPublished>;
  const empty = rows.filter((r) => r.n === 0).map((r) => r.bucket);
  return (
    <div className="space-y-3" data-testid="q-calibration">
      <div className="table-scroll">
        <table className="data-table" data-testid="q-calibration-table">
          <caption className="sr-only">Calibration: the chance shown against the share that played, by chance group</caption>
          <thead>
            <tr>
              <th scope="col">Chance group</th>
              <th scope="col" className="num">Players</th>
              <th scope="col" className="num">Average chance</th>
              <th scope="col" className="num">Share that played</th>
            </tr>
          </thead>
          <tbody>
            {[...rows].sort((a, b) => a.line - b.line).map((r) => (
              <tr key={r.line} data-bucket={r.bucket}>
                <th scope="row">{r.bucket}</th>
                <td className="num">{fmtInt(r.n)}</td>
                <td className="num">{r.predicted === null ? "–" : pct(r.predicted, 1)}</td>
                <td className="num">{r.actual === null ? "–" : pct(r.actual, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <CalibrationFigure id={id} caption="Chance he plays against how often he played" points={points} observedLabel="Really played">
        Walk-forward, every tagged player of the test seasons.{empty.length ? ` No player fell in ${empty.join(", ")}.` : ""}
      </CalibrationFigure>
    </div>
  );
}

/** The season's live record: graded players, the share that played and their average chance (a
 *  [data-live] block: "graded", "pending" or "none", as /track-record's other live blocks). */
export function LiveRecord({ rows, season }: { rows: QLiveRow[]; season: number | null }) {
  const rec = liveRecord(rows, season);
  const all = rec?.all;
  if (!all || all.n === 0 || all.actual === null) {
    return (
      <div data-testid="q-live-empty" data-live={all?.pending ? "pending" : "none"}>
        <p className="font-semibold">No live results yet.</p>
        <p className="mt-1 text-sm text-muted">
          {all?.pending ? `${fmtInt(all.pending)} listed ${all.pending === 1 ? "player is" : "players are"} waiting for their game. ` : ""}A player is graded once his
          game&apos;s snap counts are in: did he take at least one offensive snap?
        </p>
      </div>
    );
  }
  const played = Math.round(all.actual * all.n);
  const weeks = all.weeks ?? 0;
  const stat = (r: QLiveRow) => ({
    label: `Played, ${r.reportStatus}`,
    value: r.actual === null ? "–" : pct(r.actual),
    note: `${fmtInt(r.n)} graded; average chance ${r.predicted === null ? "–" : pct(r.predicted)}`,
  });
  const tiles = rec.byStatus.filter((r) => r.n > 0);
  return (
    <div className="space-y-3" data-testid="q-live" data-live="graded">
      <p>
        <strong className="tnum">{fmtInt(played)}</strong> of the <strong className="tnum">{fmtInt(all.n)}</strong> graded players played (
        <strong className="tnum">{pct(all.actual, 1)}</strong>), from {fmtInt(weeks)} live {weeks === 1 ? "week" : "weeks"} of lists; the{" "}
        <Term name="play_chance">chance</Term> they were given averaged {all.predicted === null ? "–" : pct(all.predicted, 1)}.
      </p>
      {tiles.length ? <StatTiles testId="q-live-tiles" stats={tiles.map(stat)} /> : null}
      <p className="text-sm text-muted">
        Each player&apos;s last list before his kickoff counts once.
        {all.pending ? ` ${fmtInt(all.pending)} more ${all.pending === 1 ? "is" : "are"} waiting for their game.` : ""}
      </p>
    </div>
  );
}
