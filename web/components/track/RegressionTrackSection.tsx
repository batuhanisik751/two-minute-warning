import Link from "next/link";
import RegressionTrack from "@/components/RegressionTrack";
import Term from "@/components/Term";
import { fmtInt, seasonWeek } from "@/lib/format";
import { getRegressionParams, getRegressionTrack } from "@/lib/queries/regression";
import { getRegressionLive } from "@/lib/queries/track-record";
import { TAGS, tagTitle, testSeasons } from "@/lib/regression";
import { liveTagSummary } from "@/lib/track-record";
import LiveResults from "./LiveResults";
import { H4, NotPublished, Panel } from "./parts";
import RegressionChoices from "./RegressionChoices";

/** /track-record#regression: Regression Watch's walk-forward backtest (the same honest track
 *  record as /methodology, reused), what each test season used, and the live lists' tags. */
export default async function RegressionTrackSection() {
  const [rows, params, live] = await Promise.all([getRegressionTrack(), getRegressionParams(), getRegressionLive()]);
  const seasons = testSeasons(rows);
  const tags = liveTagSummary(live.rows, TAGS);
  return (
    <>
      <Panel kind="backtest" title="The backtest" context="Regression Watch" id="regression-backtest">
        {rows.length ? (
          <>
            <RegressionTrack rows={rows} weeks={params?.weeks ?? []} />
            <H4>Season by season</H4>
            <NotPublished what="Each season's own test results">
              The track record publishes the {seasons ? `${seasons.from}–${seasons.to}` : ""} test seasons pooled only. The table below
              shows what each test season used instead, chosen on the seasons before it.
            </NotPublished>
            <RegressionChoices rows={rows} />
          </>
        ) : (
          <NotPublished what="Regression Watch's backtest" />
        )}
        <H4>Calibration</H4>
        <NotPublished what="A calibration plot">
          Regression Watch projects <Term name="ppg">points per game</Term> and tags players; it gives no probability, and no calibration groups are
          published for it.
        </NotPublished>
        <p className="text-sm">
          <Link href="/methodology#regression">How Regression Watch works, by position, and the stability study</Link>
        </p>
      </Panel>
      <Panel kind="live" title="Live this season" context="Regression Watch" id="regression-live">
        {live.lists.length ? (
          <p data-testid="rw-live-lists">
            {fmtInt(live.lists.length)} live weekly {live.lists.length === 1 ? "list" : "lists"} so far:{" "}
            {live.lists.map((l) => seasonWeek(l.season, l.week)).join(", ")}.
          </p>
        ) : null}
        {TAGS.map((t) => (
          <div key={t}>
            <H4>{tagTitle(t)}</H4>
            <LiveResults
              summary={tags[t]}
              what={`${tagTitle(t)} tags`}
              success="came true"
              lists="weekly lists"
              pendingUntil="the regular season ends (the outcome is his rest-of-season points per game)"
              testId={`rw-live-${t}`}
              emptyText={live.lists.length ? `No ${tagTitle(t)} tag in the live lists so far.` : undefined}
            />
          </div>
        ))}
      </Panel>
    </>
  );
}
