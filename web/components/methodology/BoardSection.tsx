import Link from "next/link";
import Term from "@/components/Term";
import { EmptyState } from "@/components/ui";
import { fmtInt, pct } from "@/lib/format";
import { showThirdPartyRanks } from "@/lib/third-party";
import { BOARD_CLIFF_DROP, BOARD_ECR_FIRST_SEASON, BOARD_KICKOFF_LEAD_HOURS, BOARD_MIN_GAMES, BOARD_MIN_PRIOR, BOARD_RANK_MIN_GAMES, BOARD_TOP_PPG, TRACK_INTERVAL_LEVEL } from "@/lib/method";
import { getBoardGraded, getBoardIndex, getBoardModels, getBoardTrack, type BoardModel } from "@/lib/queries/board";
import BoardBacktest from "./BoardBacktest";
import { BoardBreakout, BoardLimits } from "./BoardText";

const H3 = ({ id, children }: { id: string; children: React.ReactNode }) => (
  <h3 id={id} className="display mt-8 scroll-mt-24 text-2xl uppercase">
    {children}
  </h3>
);

function Features({ m, testId }: { m: BoardModel; testId: string }) {
  return (
    <ul className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2" data-testid={testId}>
      {m.featureList.map((f) => (
        <li key={f}>
          <Term name={f} />
        </li>
      ))}
    </ul>
  );
}

const fit = (m: BoardModel) =>
  `trained on the boards of ${m.trainingSeasons[0] + 1}–${m.trainingSeasons[m.trainingSeasons.length - 1] + 1} (version ${m.modelVersion}${m.c !== null ? `, penalty strength C = ${m.c}` : ""})`;

/** /methodology's board section: who is on it, the two labels, the snapshot and its timing, the
 *  features, the two models, the backtest with intervals, the Breakout result (research only) and
 *  the limits. Numbers from the board tables (or lib/method.ts). */
export default async function BoardSection() {
  const [track, models, graded, index] = await Promise.all([getBoardTrack(), getBoardModels(), getBoardGraded(), getBoardIndex()]);
  const { cliff, missed } = models;
  return (
    <section aria-labelledby="board" data-testid="board-method">
      <h2 id="board" className="section-title scroll-mt-24">
        The Cliff board
      </h2>
      {!track.length && !cliff ? (
        <div className="mt-3">
          <EmptyState title="No board published yet" />
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <p>
            Before each season, two <Term name="board_cliff_chance">estimated chances</Term> for every established veteran (
            <Link href="/board">the Cliff board</Link>){showThirdPartyRanks() ? ", next to the experts' preseason ranks" : ""}: a Cliff, and missed time. Estimates from
            past seasons&apos; patterns, worded as such.
          </p>
          <H3 id="board-labels">Who is on it, and the two outcomes</H3>
          <p>
            A player is on the board of a season when he had {BOARD_MIN_PRIOR} or more seasons in the league before last season and finished
            last season in the top {BOARD_TOP_PPG} at his position in <Term name="ppg">points per game</Term> (ranked among players with {BOARD_RANK_MIN_GAMES} or
            more games). The season after decides: <Term name="y_missed">missed time</Term> is fewer than {BOARD_MIN_GAMES} games (an injury,
            a benching, a release or retirement; no games counts); a <Term name="y_cliff">Cliff</Term> is {BOARD_MIN_GAMES} or more games and
            points per game down by {pct(BOARD_CLIFF_DROP)} or more. The Cliff model learns only from players with {BOARD_MIN_GAMES}+ games,
            missed time has its own simpler model, and a check that counts missed time as a Cliff is reported beside them.
          </p>
          <H3 id="board-snapshot">When the board is read</H3>
          <p>
            On the eve of week 1: {BOARD_KICKOFF_LEAD_HOURS} {BOARD_KICKOFF_LEAD_HOURS === 1 ? "hour" : "hours"} before the season&apos;s first
            kickoff, through the same <Term name="as_of">point-in-time</Term> view as every other module. Last season&apos;s numbers are all
            public by then, and so are the week-1 depth charts: who is on which team, his spot on the chart, a newcomer ahead of him, a new
            starting quarterback or head coach, and the targets and carries the team lost. The project&apos;s plan named the Tuesday before
            week 1, but no depth chart is public then for the seasons the model learns from, so the board moved to the eve of week 1 (the
            owner&apos;s decision). Next August&apos;s live board may be published earlier, from the latest daily depth chart; the board will
            then say so.
          </p>
          {cliff || missed ? <H3 id="board-features">The features</H3> : null}
          {cliff ? (
            <>
              <p>The chance of a Cliff: {fmtInt(cliff.featureList.length)} inputs (hover or tap for the definition).</p>
              <Features m={cliff} testId="board-cliff-features" />
            </>
          ) : null}
          {missed ? (
            <>
              <p>The chance of missed time: {fmtInt(missed.featureList.length)} inputs.</p>
              <Features m={missed} testId="board-missed-features" />
            </>
          ) : null}
          <p className="text-sm text-muted">
            A missing input (no depth chart, a statistic that starts in a later season) gets the training median plus a missing flag.
          </p>
          <H3 id="board-models">The models</H3>
          <p>
            Two penalized (L2) logistic regressions, refit for each board on every earlier board; the penalty is chosen inside each fit on
            its own training seasons only, and each model&apos;s own probability is shown.
            {cliff ? ` The chance of a Cliff: ${fit(cliff)}.` : ""}
            {missed ? ` The chance of missed time: ${fit(missed)}.` : ""} The drivers on each row are the regression&apos;s own terms.
          </p>
          <H3 id="board-backtest">The backtest</H3>
          <p>
            Every past board reconstructed by models that learned only from earlier boards
            {graded.from !== null && graded.to !== null ? ` (${fmtInt(graded.boards)} boards, ${graded.from}–${graded.to})` : ""}. <Term name="pr_auc">PR-AUC</Term>{" "}
            measures how well the order puts the players who really had the outcome near the top; differences are paired, with{" "}
            {pct(TRACK_INTERVAL_LEVEL)} intervals from redrawing whole seasons. The experts&apos; era is the boards from{" "}
            {BOARD_ECR_FIRST_SEASON} on.
          </p>
          <BoardBacktest rows={track.filter((r) => !r.research)} variants={["cliff_main", "cliff_missed", "cliff_sensitivity"]} testId="board-backtest" />
          <H3 id="board-breakout">Breakout: researched, not shown</H3>
          <BoardBreakout rows={track.filter((r) => r.research)} />
          <H3 id="board-limits">Limits</H3>
          <BoardLimits liveBoards={index.filter((r) => r.kind === "live").length} />
        </div>
      )}
    </section>
  );
}
