import Link from "next/link";
import PhaseCalibration from "@/components/hot-seat/PhaseCalibration";
import Term from "@/components/Term";
import { StatTiles } from "@/components/track/parts";
import { EmptyState } from "@/components/ui";
import { fmtInt } from "@/lib/format";
import { calibrationGrid, headlineStats, metricWithInterval, trackCell } from "@/lib/hot-seat";
import { getHotSeatCalibration, getHotSeatFirings, getHotSeatIndex, getHotSeatModel, getHotSeatTrack } from "@/lib/queries/hot-seat";
import HotSeatFirings from "./HotSeatFirings";
import HotSeatModels from "./HotSeatModels";
import { HotSeatLabels, HotSeatLimits, HotSeatResearch } from "./HotSeatText";

const H3 = ({ id, children }: { id: string; children: React.ReactNode }) => (
  <h3 id={id} className="display mt-8 scroll-mt-24 text-2xl uppercase">
    {children}
  </h3>
);

/** /methodology's Hot-Seat section: labels and windows, features, the model and why, the backtest's
 *  headline numbers with intervals, firings per season, calibration by season phase, the research
 *  result and the limits. Numbers from the hot_seat_* tables (or lib/method.ts). */
export default async function HotSeatSection() {
  const [track, firings, model, index, cal] = await Promise.all([getHotSeatTrack(), getHotSeatFirings(), getHotSeatModel(), getHotSeatIndex(), getHotSeatCalibration()]);
  const tested = index.filter((r) => r.kind === "backtest").map((r) => r.season);
  const firstTest = tested.length ? Math.min(...tested) : null;
  const testFirings = firings.filter((f) => firstTest !== null && f.season >= firstTest && f.season < (model.live?.testSeason ?? Infinity));
  const counts = testFirings.map((f) => f.positiveDepartures);
  const own = trackCell(track, { model: "logit", slice: "all", metric: "brier" });
  const iso = trackCell(track, { model: "logit", slice: "all", metric: "brier", prob: "prob_iso" });
  const m = model.live;
  return (
    <section aria-labelledby="hot-seat" data-testid="hot-seat-method">
      <h2 id="hot-seat" className="section-title scroll-mt-24">
        The Hot-Seat Meter
      </h2>
      {!track.length && !m ? (
        <div className="mt-3">
          <EmptyState title="No Hot-Seat Meter published yet" />
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <p>
            An <Term name="hot_seat_estimate">estimated chance</Term>, for every head coach each week, of being let go (
            <Link href="/hot-seat">the Hot-Seat Meter</Link>). These are real people&apos;s jobs: the numbers are estimates
            from past seasons&apos; patterns, worded as such, and never a verdict.
          </p>
          <HotSeatLabels />
          <H3 id="hot-seat-features">The features</H3>
          {m ? (
            <>
              <p>
                {fmtInt(m.featureList.length)} features, each computed at the list&apos;s as-of time from games already played
                (hover or tap for the definition):
              </p>
              <ul className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2" data-testid="hot-seat-features">
                {m.featureList.map((f) => (
                  <li key={f}>
                    <Term name={f} />
                  </li>
                ))}
              </ul>
              <p className="text-sm text-muted">
                Missing values (fourth-down grades before they exist) get the training median plus a missing flag. Running
                totals that only grow with the week are left out; their differences (wins against expectation) are in.
              </p>
            </>
          ) : null}
          <H3 id="hot-seat-model">The model, and why a logistic regression</H3>
          <p>
            A penalized (L2) logistic regression on whether the coach was let go
            {m ? (
              <>
                , refit for each season on every earlier season ({m.trainingSeasons[0]}–{m.trainingSeasons[m.trainingSeasons.length - 1]} for{" "}
                {m.testSeason}; version <span className="font-mono text-xs">{m.modelVersion}</span>
                {m.c !== null ? `, penalty strength C = ${m.c}` : ""})
              </>
            ) : null}
            . The penalty is chosen inside each fit on its own training seasons only. It was compared in a{" "}
            <Term name="walk_forward">walk-forward backtest</Term> with a discrete-time hazard model and LightGBM: LightGBM did not
            beat it, the hazard model was about as good, and a logistic regression&apos;s terms add up to its estimate, which is
            what the drivers on each row show. Its own probabilities are used (<Term name="brier">Brier</Term> {metricWithInterval(own, 4)}; the isotonic
            recalibration scored {metricWithInterval(iso, 4)}).
          </p>
          <HotSeatModels rows={track} />
          <H3 id="hot-seat-backtest">The backtest in three numbers</H3>
          <StatTiles testId="hot-seat-headline" stats={headlineStats(track)} />
          <H3 id="hot-seat-firings">Firings per season</H3>
          <p className="text-sm text-muted">Coach-seasons by what happened; interim coaches counted apart.</p>
          <HotSeatFirings rows={firings} firstTest={firstTest} />
          <H3 id="hot-seat-calibration">Calibration by season phase</H3>
          <p>
            Within each phase and estimate band: the average estimate against how often those coaches were really let go, over
            every reconstructed list with a known outcome (interim coaches left out).
          </p>
          <PhaseCalibration groups={calibrationGrid(cal)} idPrefix="method-cal" />
          <p id="method-cal-note" className="text-sm text-muted">
            A coach appears once per week, so one coach-season counts several times.
          </p>
          <H3 id="hot-seat-research">Do fourth-down decisions get coaches fired?</H3>
          <HotSeatResearch />
          <H3 id="hot-seat-limits">Limits</H3>
          <HotSeatLimits fewest={counts.length ? Math.min(...counts) : null} most={counts.length ? Math.max(...counts) : null} />
        </div>
      )}
    </section>
  );
}
