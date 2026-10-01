import Link from "next/link";
import Term from "@/components/Term";
import { fmtInt, pct, pctRange, points, pointsRange } from "@/lib/format";
import { TRACK_INTERVAL_LEVEL } from "@/lib/method";
import { positionLabel, positionShort, sortPositions } from "@/lib/positions";
import { getGlossary } from "@/lib/queries/glossary";
import { getStreamModels, getStreamTrack } from "@/lib/queries/stream";
import { getStreamLivePicks, getStreamSeasonRows } from "@/lib/queries/track-record";
import { isBaselineMethod, starterWeek, streamComparison, streamMethodName, streamMethodOf, streamTopN, verdictWords, type StreamTrackRow } from "@/lib/streamer";
import { liveSummary, streamCalibration, streamSeasons, topNOf } from "@/lib/track-record";
import CalibrationFigure from "./CalibrationFigure";
import LiveResults from "./LiveResults";
import { H4, NotPublished, Panel, StatTiles } from "./parts";
import StreamSeasons from "./StreamSeasons";

const iv = (lo: number | null, hi: number | null, f: (a: number, b: number) => string) =>
  lo !== null && hi !== null ? `${pct(TRACK_INTERVAL_LEVEL)} interval ${f(lo, hi)}` : null;

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** A method's pooled row (its interval) for the headline. */
const pooled = (rows: StreamTrackRow[], position: string, method: string, metric: string) =>
  rows.find((r) => r.position === position && r.scope === "pooled" && r.trainOn === "pool" && r.method === method && r.metric === metric);
const rowIv = (r: StreamTrackRow | undefined) => (r ? iv(r.lo, r.hi, (a, b) => pctRange(a, b, 1)) : null);

/** /track-record#streamer: per position, the K and D/ST streamer's backtest (the method on the site
 *  against its strongest rival), its seasons and calibration, and its live lists. */
export default async function StreamTrack() {
  const [rows, seasonRows, models, gloss, live] = await Promise.all([getStreamTrack(), getStreamSeasonRows(), getStreamModels(), getGlossary(), getStreamLivePicks()]);
  const starters = streamTopN(gloss);
  const positions = sortPositions(new Set([...rows.map((r) => r.position), ...Object.keys(models)]));
  if (!positions.length) {
    return (
      <>
        <Panel kind="backtest" title="The backtest" context="K and D/ST streamer" id="streamer-backtest">
          <NotPublished what="The K and D/ST streamer's track record" />
        </Panel>
        <Panel kind="live" title="Live this season" context="K and D/ST streamer" id="streamer-live">
          <LiveResults summary={liveSummary([], 0)} what="picks" success="hit" pendingUntil="the next week's games are final" testId="stream-live" />
        </Panel>
      </>
    );
  }
  return (
    <>
      {positions.map((p) => {
        const c = models[p] ? streamComparison(rows, p, streamMethodOf(models[p])) : null;
        const topN = c ? topNOf(c.metric) : null;
        const starter = starterWeek(starters, p);
        const cal = streamCalibration(rows, p);
        return (
          <div key={p} className="mt-6" data-testid={`track-stream-${p}`}>
            <Panel kind="backtest" title={`${positionLabel(p)}: the backtest`} id={`streamer-${p}-backtest`}>
              {c && topN ? (
                <>
                  <p>
                    {c.seasons.replace("-", "–")}: how often the top {topN} of each weekly list had a{" "}
                    <Term name="y_start">{starter}</Term> the very next week, for the method on the site and its strongest rival
                    {isBaselineMethod(c.ours.method) ? " (the site uses a simple rule here: it did as well as the models)" : ""}.
                  </p>
                  <StatTiles
                    testId={`stream-headline-${p}`}
                    stats={[
                      { label: `${cap(streamMethodName(c.ours.method, p))} (on the site)`, value: pct(c.ours.value, 1), interval: rowIv(pooled(rows, p, c.ours.method, c.metric)), note: c.baseRate !== null ? `a random pool pick: ${pct(c.baseRate, 1)}` : null },
                      { label: cap(streamMethodName(c.other.method, p)), value: pct(c.other.value, 1), interval: rowIv(pooled(rows, p, c.other.method, c.metric)), note: "the strongest rival" },
                      ...(c.diff && c.verdict
                        ? [{ label: "The site's method minus the rival", value: points(c.diff.value), interval: iv(c.diff.lo, c.diff.hi, pointsRange), note: verdictWords(c.verdict) }]
                        : []),
                    ]}
                  />
                  <H4>Season by season</H4>
                  {(() => {
                    const s = streamSeasons(seasonRows, p, c.ours.method, c.other.method, c.metric);
                    return s.length ? <StreamSeasons rows={s} position={p} ours={c.ours.method} other={c.other.method} topN={topN} starter={starter} /> : <NotPublished what="The per-season results" />;
                  })()}
                </>
              ) : (
                <NotPublished what={`The ${positionLabel(p)} backtest`} />
              )}
              <H4>Calibration</H4>
              {cal.method && cal.points.length ? (
                <CalibrationFigure id={`stream-calibration-${p}`} caption={`Is the ${positionShort(p)} model's probability right?`} points={cal.points} observedLabel={`Really had a ${starter}`}>
                  Every pool pick of every backtest list, grouped by the probability of {streamMethodName(cal.method, p)}
                  {c && cal.method !== c.ours.method ? `, which the site does not use for ${positionLabel(p)}` : ""}.
                  {cal.points.some((x) => x.n !== null) ? ` ${fmtInt(cal.points.reduce((a, x) => a + (x.n ?? 0), 0))} predictions.` : ""}
                </CalibrationFigure>
              ) : (
                <NotPublished what={`The ${positionShort(p)} calibration`} />
              )}
            </Panel>
            <Panel kind="live" title={`${positionLabel(p)}: live this season`} id={`streamer-${p}-live`}>
              {topN || !live.some((x) => x.position === p) ? (
                <LiveResults summary={liveSummary(live.filter((x) => x.position === p), topN ?? 0)} what={`top-${topN} picks`} success={`had a ${starter}`} pendingUntil="the next week's games are final" testId={`stream-live-${p}`} />
              ) : (
                <NotPublished what="The live results" />
              )}
            </Panel>
          </div>
        );
      })}
      <p className="mt-4 text-sm">
        <Link href="/methodology#streamer">How the streamer works, and every method&apos;s results</Link>
      </p>
    </>
  );
}
