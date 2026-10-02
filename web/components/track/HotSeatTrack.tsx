import Link from "next/link";
import PhaseCalibration from "@/components/hot-seat/PhaseCalibration";
import Term from "@/components/Term";
import { fmtInt, pct } from "@/lib/format";
import { calibrationGrid, headlineStats, listName } from "@/lib/hot-seat";
import { HOT_SEAT_WINDOW_DAYS } from "@/lib/method";
import { getHotSeatCalibration, getHotSeatIndex, getHotSeatLive, getHotSeatTrack } from "@/lib/queries/hot-seat";
import { H4, NotPublished, Panel, StatTiles } from "./parts";

/** /track-record#hot-seat: the walk-forward backtest (reconstructed lists, final outcomes) and the
 *  live lists (outcomes pending until the season's departures are labelled), kept apart. */
export default async function HotSeatTrack() {
  const [track, cal, live, index] = await Promise.all([getHotSeatTrack(), getHotSeatCalibration(), getHotSeatLive(), getHotSeatIndex()]);
  const stats = headlineStats(track);
  const groups = calibrationGrid(cal);
  const newest = index[0];
  return (
    <>
      <Panel kind="backtest" title="The backtest" context="Hot-Seat Meter" id="hot-seat-backtest">
        {stats.length ? (
          <>
            <p>
              Every past season is estimated by a model that learned only from the seasons before it, at each{" "}
              <Term name="as_of">as-of time</Term>, and compared with who was really <Term name="hot_seat_let_go">let go</Term>{" "}
              by {HOT_SEAT_WINDOW_DAYS} days after the season. Interim coaches are left out of these numbers.
            </p>
            <StatTiles testId="hot-seat-headline" stats={stats} />
          </>
        ) : (
          <NotPublished what="The Hot-Seat backtest" />
        )}
        <H4>Calibration by season phase</H4>
        {groups.length ? (
          <>
            <PhaseCalibration groups={groups} idPrefix="track-cal" />
            <p id="track-cal-note" className="text-sm text-muted">
              Average estimate against how often the coaches were really let go; a coach appears once per week, so one
              coach-season counts several times.
            </p>
          </>
        ) : (
          <NotPublished what="The Hot-Seat calibration" />
        )}
        <p className="text-sm">
          <Link href="/methodology#hot-seat">Every model compared, firings per season and the limits</Link>
        </p>
      </Panel>
      <Panel kind="live" title="Live this season" context="Hot-Seat Meter" id="hot-seat-live">
        <div data-testid="hot-seat-live" data-live={live.lists ? (live.final ? "graded" : "pending") : "none"}>
          {live.final ? (
            <p>
              {fmtInt(live.letGo)} of the {fmtInt(live.final)} graded coach-weeks ended with the coach let go (
              {pct(live.letGo / live.final, 1)}), from {fmtInt(live.lists)} live {live.lists === 1 ? "list" : "lists"}. The rest
              are pending until the season&apos;s departures are labelled.
            </p>
          ) : (
            <>
              <p className="font-semibold">No live results yet.</p>
              <p className="mt-1 text-sm text-muted">
                {live.lists
                  ? `${fmtInt(live.lists)} live ${live.lists === 1 ? "list" : "lists"} (${fmtInt(live.coachSeasons)} coach-seasons): outcomes pending until the season's departures are labelled. Nothing is graded before then.`
                  : `${newest ? `No list has been made live yet: the newest (${listName(newest.season, newest.week, newest.snapshot)}) was reconstructed after its week. ` : "No list has been made live yet. "}Live outcomes will stay pending until the season's departures are labelled.`}
              </p>
            </>
          )}
        </div>
      </Panel>
    </>
  );
}
