import Link from "next/link";
import Term from "@/components/Term";
import { fmtInt, pct } from "@/lib/format";
import { TRACK_INTERVAL_LEVEL } from "@/lib/method";
import { wpComparison, type Cell } from "@/lib/decisions-track";
import { getDecisionsMeta, getDecisionsTrack } from "@/lib/queries/decisions";
import { wpCalibration, wpSeasons } from "@/lib/track-record";
import CalibrationFigure from "./CalibrationFigure";
import { H4, NotPublished, Panel, StatTiles } from "./parts";
import WpSeasons from "./WpSeasons";

const f4 = (x: number, signed = false) => `${signed && x > 0 ? "+" : ""}${x.toFixed(4)}`;
const iv = (c: Pick<Cell, "lo" | "hi">, signed = false) =>
  c.lo !== null && c.hi !== null ? `${pct(TRACK_INTERVAL_LEVEL)} interval ${f4(c.lo, signed)} to ${f4(c.hi, signed)}` : null;
/** A log-loss difference (ours minus theirs) in words: lower is better. */
const verdict = (c: Cell) => (c.hi !== null && c.hi < 0 ? "ours is lower (better), clearly" : c.lo !== null && c.lo > 0 ? "ours is higher (worse), clearly" : "no clear difference");
const NAMES: Record<string, string> = { nflfastr_wp: "nflfastR wp", nflfastr_vegas_wp: "nflfastR vegas_wp" };

/** /track-record#decisions: the Decision Report Card's win-probability model, the base of every
 *  grade: its walk-forward test (pooled, by season) and its reliability. */
export default async function DecisionsTrack() {
  const [rows, meta] = await Promise.all([getDecisionsTrack(), getDecisionsMeta()]);
  const c = wpComparison(rows);
  const own = c?.methods.find((m) => m.method === "own");
  const seasons = wpSeasons(rows);
  const cal = wpCalibration(rows, "own");
  return (
    <>
      <Panel kind="backtest" title="The backtest" context="Decision Report Card" id="decisions-backtest">
        {c && own?.logLoss ? (
          <>
            <p>
              Every grade rests on <Term name="own_wp">our win-probability model</Term>. Each test season {c.span.replace("-", "–")} is
              scored by a model that learned only from earlier seasons, on {c.n !== null ? fmtInt(c.n) : "every"} plays, and compared
              with nflfastR&apos;s two public models (partly in-sample: they were fit on these seasons too). Log loss: lower is better.
            </p>
            <StatTiles
              testId="decisions-headline"
              stats={[
                { label: "Our model's log loss", value: f4(own.logLoss.value), interval: iv(own.logLoss), note: own.brier ? `Brier score ${f4(own.brier.value)}` : null },
                ...c.diffs
                  .filter((d): d is typeof d & { logLoss: Cell } => d.logLoss !== null)
                  .map((d) => ({ label: `Ours minus ${NAMES[d.method] ?? d.method}`, value: f4(d.logLoss.value, true), interval: iv(d.logLoss, true), note: verdict(d.logLoss) })),
              ]}
            />
            <H4>Season by season</H4>
            {seasons.length ? <WpSeasons rows={seasons} /> : <NotPublished what="The per-season results" />}
          </>
        ) : (
          <NotPublished what="The win-probability model's backtest" />
        )}
        <H4>Calibration</H4>
        {cal.length ? (
          <CalibrationFigure id="wp-calibration" caption="Is our win probability right?" points={cal} observedLabel="Really won">
            Every test play{c ? ` of ${c.span.replace("-", "–")}` : ""}, grouped by our model&apos;s win probability, with the{" "}
            {pct(TRACK_INTERVAL_LEVEL)} interval of how often the team really won.
          </CalibrationFigure>
        ) : (
          <NotPublished what="The win-probability model's calibration" />
        )}
        <p className="text-sm">
          <Link href="/methodology#decisions">The sub-models, the nfl4th benchmark and the grading rules</Link>
        </p>
      </Panel>
      <Panel kind="live" title="Live this season" context="Decision Report Card" id="decisions-live">
        <div data-testid="decisions-live" data-live="none">
          <p className="font-semibold">No live results yet.</p>
          <p className="mt-1 text-sm text-muted">
            The model&apos;s accuracy on this season&apos;s plays is not published yet. This season&apos;s grades
            {meta.season !== null ? ` (${meta.season}${meta.latestWeek !== null ? `, through week ${meta.latestWeek}` : ""})` : ""} are on{" "}
            <Link href="/decisions">the Report Card</Link>.
          </p>
        </div>
      </Panel>
    </>
  );
}
