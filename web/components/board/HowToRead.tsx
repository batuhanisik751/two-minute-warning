import Term from "@/components/Term";
import { boardCalibration, type BoardCalCell, type DisagreementRow } from "@/lib/board";
import { BOARD_CLIFF_DROP, BOARD_DISAGREE_TOP, BOARD_DRIVERS, BOARD_ECR_FIRST_SEASON, BOARD_KICKOFF_LEAD_HOURS, BOARD_MIN_GAMES } from "@/lib/method";
import BoardCalibration from "./BoardCalibration";
import DisagreementRecord from "./DisagreementRecord";

const pct = (x: number) => `${Math.round(x * 100)}%`;

/** /board's "How to read this": what each chance means, the timing, the experts' rank and the
 *  disagreement rule, calibration by band from the published boards and outcomes, and how past
 *  disagreements turned out. */
export default function HowToRead({ cal, record }: { cal: BoardCalCell[]; record: DisagreementRow[] }) {
  const groups = boardCalibration(cal);
  return (
    <section aria-labelledby="how-to-read" className="mt-10 max-w-4xl" data-testid="how-to-read">
      <h2 id="how-to-read" className="section-title mb-3 scroll-mt-24">
        How to read this
      </h2>
      <div className="space-y-3">
        <p>
          <strong>Two separate chances</strong>, each a statistical estimate from how past seasons went, made only from what was public at
          the time. The <Term name="board_cliff_chance">chance of a Cliff</Term> is the estimated chance that he plays {BOARD_MIN_GAMES} or more
          games next season and his points per game drop by {pct(BOARD_CLIFF_DROP)} or more. It only means something if he plays: the model
          learned it from players who did. The <Term name="board_missed_chance">chance of missed time</Term> is the estimated chance that he
          plays fewer than {BOARD_MIN_GAMES} games (an injury, a benching, a release or retirement), from its own simpler model. The two
          answer different questions and are never added together.
        </p>
        <p>
          <strong>When it is made.</strong> The model learns from the eve of week 1 ({BOARD_KICKOFF_LEAD_HOURS} {BOARD_KICKOFF_LEAD_HOURS === 1 ? "hour" : "hours"} before the first kickoff),
          when the week-1 depth charts are public, and every board here is read at that moment. Next August&apos;s live board may be published
          earlier, from the latest daily depth chart; if so, the note at the top of the board says so and its as-of time shows when.
        </p>
        <p>
          <strong>The experts.</strong> Beside the chances is FantasyPros&apos; <Term name="board_ecr">preseason expert consensus rank (ECR)</Term>{" "}
          at his position: the experts&apos; combined ranking, from {BOARD_ECR_FIRST_SEASON} on. It is not our model. <Term name="board_disagree">Where we disagree</Term>{" "}
          marks a player in our top {BOARD_DISAGREE_TOP} by the chance of a Cliff who is not among the {BOARD_DISAGREE_TOP} players the experts
          drop furthest below last season&apos;s finish (their rank minus last season&apos;s rank; a player they did not rank counts as dropped
          furthest), or the reverse. The {BOARD_DRIVERS} lines under each chance are what moves it most compared with an average player.
        </p>
      </div>
      <h3 className="display mt-6 mb-2 text-xl uppercase" id="board-calibration-heading">
        Do the chances mean what they say?
      </h3>
      {groups.length ? (
        <>
          <p className="mb-3">
            If the estimates are right, players given a certain chance have that outcome about that often. Every reconstructed board with a
            known outcome, by band:
          </p>
          <BoardCalibration groups={groups} />
          <p id="board-cal-note" className="mt-2 text-sm text-muted">
            The Cliff is counted among players who played {BOARD_MIN_GAMES} or more games; missed time among every player on the boards.
          </p>
        </>
      ) : (
        <p className="text-muted">The check needs reconstructed boards with known outcomes; none is published yet.</p>
      )}
      <h3 className="display mt-6 mb-2 text-xl uppercase" id="board-record">
        How past disagreements turned out
      </h3>
      <DisagreementRecord rows={record} variant="cliff_main" />
      <h4 className="mt-5 mb-2 font-display text-lg font-bold tracking-wide uppercase">The same comparison for missed time</h4>
      <DisagreementRecord rows={record} variant="cliff_missed" />
    </section>
  );
}
