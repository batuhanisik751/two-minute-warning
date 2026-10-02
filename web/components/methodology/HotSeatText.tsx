import Link from "next/link";
import Term from "@/components/Term";
import { fmtInt } from "@/lib/format";
import { RESEARCH_URL } from "@/lib/hot-seat";
import { AS_OF_TIME_UTC, AS_OF_WEEKDAY, HOT_SEAT_FIRST_WEEK, HOT_SEAT_RESEARCH, HOT_SEAT_WINDOW_DAYS } from "@/lib/method";

/** What counts as "let go", the windows and snapshots, and where the labels come from
 *  (docs/hot_seat.md "Labels", "Rows", "Where the labels come from"). */
export function HotSeatLabels() {
  return (
    <>
      <p>
        <strong>What is estimated.</strong> For each head coach at each point of the season: the chance that he is{" "}
        <Term name="hot_seat_let_go">let go</Term> (fired during the season, fired after it, or a mutual parting) and that it
        is announced on or after that day and no later than {HOT_SEAT_WINDOW_DAYS} days after his team&apos;s final game,
        playoffs included. Other departures (retired, resigned, including under pressure, left for another job) count as not
        let go and are reported apart. <Term name="is_interim">Interim coaches</Term> are scored and flagged but never trained
        on.
      </p>
      <p>
        <strong>When.</strong> A weekly list every {AS_OF_WEEKDAY} at {AS_OF_TIME_UTC} UTC from week {HOT_SEAT_FIRST_WEEK} (a
        team needs a played game) to the week before the last; then an <strong>end-of-season snapshot</strong>, each team as of
        its own last regular-season game. The last week&apos;s {AS_OF_WEEKDAY} list is not made: it would come after the
        Monday when most end-of-season firings are announced. Every list uses only what was public at its{" "}
        <Term name="as_of">as-of time</Term>.
      </p>
      <p>
        <strong>Where the labels come from.</strong> Each departure&apos;s type, date and source were researched from cited
        public pages (mostly each season&apos;s Wikipedia &ldquo;NFL season&rdquo; page and the coaches&apos; own pages, with
        the link and a quote per row). The project&apos;s owner accepted all of them in bulk rather than re-checking each one,
        and filled the one row the research could not settle from the coach&apos;s own page.
      </p>
    </>
  );
}

const or2 = (x: number) => x.toFixed(2);

/** Step H5 in a paragraph: does poor fourth-down decision-making raise firing risk? No detectable link. */
export function HotSeatResearch() {
  const R = HOT_SEAT_RESEARCH;
  return (
    <div className="space-y-3" data-testid="hot-seat-research">
      <p>
        Controlling for results against the market&apos;s expectation and the usual context (tenure, last season&apos;s
        playoffs, losing streaks, a rookie first-round quarterback, division rank), coaches who give away more win probability
        on fourth down (the <Link href="/decisions">Decision Report Card</Link>&apos;s measure) were let go at season end about
        as often as the others: <strong>no detectable link</strong>. The odds ratio per standard deviation of fourth-down WP
        lost per game is {or2(R.oddsRatio)}, with an interval of {or2(R.classicalLo)} to {or2(R.classicalHi)} ({or2(R.bootstrapLo)}{" "}
        to {or2(R.bootstrapHi)} when whole seasons are resampled; 1 means no effect), and shuffling the measure among each season&apos;s
        coaches gives p = {R.permP.toFixed(2)}. The data: {fmtInt(R.coachSeasons)} coach-seasons at the end of the regular
        season over {fmtInt(R.seasons)} seasons, {fmtInt(R.positiveCoachSeasons)} of them let go.
      </p>
      <p>
        A null result is still a result: in the same model, results against the market&apos;s expectation clearly matter (odds
        ratio {or2(R.winsVsExpectedOddsRatio)} per standard deviation). It cannot rule out a modest effect either way.{" "}
        <a href={RESEARCH_URL}>The full write-up</a>.
      </p>
    </div>
  );
}

/** What the Hot-Seat Meter cannot know or do (docs/hot_seat.md; the backtest report). */
export function HotSeatLimits({ fewest, most }: { fewest: number | null; most: number | null }) {
  return (
    <ul className="list-disc space-y-1 pl-5">
      <li>
        It sees results and context only: nothing of what owners and general managers say, reports of tension, contract
        details or coordinator changes (the data has none).
      </li>
      <li>
        Firings are rare
        {fewest !== null && most !== null ? ` (${fmtInt(fewest)} to ${fmtInt(most)} coaches let go per test season)` : ""}, so
        every number here rests on few events and the intervals are wide.
      </li>
      <li>Early in the season the estimates are rough (the calibration by phase above shows how rough).</li>
      <li>Interim coaches are scored, but the model never learned from them: their estimates are the least reliable.</li>
      <li>
        The probabilities are the model&apos;s own: the isotonic recalibration the backtest also tried did worse, so it is not
        used. Every team is treated alike, although owners differ in patience.
      </li>
      <li>The outcomes come from research the owner accepted in bulk; a wrong entry would affect both training and grading.</li>
    </ul>
  );
}
