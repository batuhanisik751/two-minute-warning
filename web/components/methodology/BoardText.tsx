import { boardCell, diffWords, type BoardCell, type BoardTrackRow } from "@/lib/board";
import { signedNum } from "@/lib/hot-seat";
import { BOARD_DISAGREE_TOP, BOARD_ECR_FIRST_SEASON, BOARD_MIN_GAMES, TRACK_INTERVAL_LEVEL } from "@/lib/method";
import BoardBacktest, { BOARD_VARIANTS, type BoardVariant } from "./BoardBacktest";

const BREAKOUT: BoardVariant[] = ["breakout_wr_te", "breakout_rb"];
const VERB = { ahead: "is ahead of", behind: "is behind", "no clear difference": "shows no clear difference from", "not published": "has no published comparison with" } as const;
const WHO = { breakout_wr_te: "wide receivers and tight ends", breakout_rb: "running backs" } as Record<string, string>;
const diffText = (c: BoardCell | null) =>
  !c ? "not published" : `${signedNum(c.value, 3)}${c.lo !== null && c.hi !== null ? `; ${Math.round(TRACK_INTERVAL_LEVEL * 100)}% interval ${signedNum(c.lo, 3)} to ${signedNum(c.hi, 3)}` : ""}`;

/** The Breakout result, stated from the research rows of board_track_record: the table and, per
 *  model, the comparison with last season's PPG rank and with the experts in words. */
export function BoardBreakout({ rows }: { rows: BoardTrackRow[] }) {
  const shown = BREAKOUT.filter((v) => rows.some((r) => r.variant === v));
  const lines = shown.map((v) => {
    const m = BOARD_VARIANTS[v].model;
    const rank = boardCell(rows, { variant: v, slice: "all", model: m, metric: "pr_auc_diff", vs: "base_ppg_rank" });
    const ecr = boardCell(rows, { variant: v, slice: "ecr_era", model: m, metric: "pr_auc_diff", vs: "ecr" });
    return { v, rank, ecr, rankWords: diffWords(rank), ecrWords: diffWords(ecr) };
  });
  const ahead = lines.filter((l) => l.rankWords === "ahead");
  return (
    <div className="space-y-3" data-testid="board-breakout">
      <p>
        The board was also built to name breakouts: young wide receivers and tight ends (and, apart, running backs) entering their second
        or third season who become fantasy starters. It is not shown as a list. The owner decided so because it did not beat the simplest
        guess, last season&apos;s points-per-game rank, in the first (end-of-season) backtest. Its preseason backtest is published here as
        research:
      </p>
      <BoardBacktest rows={rows} variants={BREAKOUT} testId="board-breakout-table" first="Model" />
      <ul className="list-disc space-y-1 pl-5">
        {lines.map((l) => (
          <li key={l.v} data-variant={l.v}>
            The Breakout model for {WHO[l.v]} {VERB[l.rankWords]} last season&apos;s PPG rank (PR-AUC difference {diffText(l.rank)}) and{" "}
            {VERB[l.ecrWords]} the experts in their era ({diffText(l.ecr)}).
          </li>
        ))}
      </ul>
      {ahead.length ? (
        <p>
          Where a Breakout model is ahead of the rank here (its interval above zero),
          {ahead.every((l) => l.ecrWords !== "ahead") ? " it is not ahead of the experts in their era, and" : ""} the owner&apos;s decision
          stands until it clearly beats both: it stays research.
        </p>
      ) : null}
    </div>
  );
}

/** What the board cannot do, in words. */
export function BoardLimits({ liveBoards }: { liveBoards: number }) {
  return (
    <ul className="list-disc space-y-1 pl-5" data-testid="board-limits">
      <li>
        The experts&apos; rank is FantasyPros&apos; preseason consensus at the position, not draft position (ADP); it exists from{" "}
        {BOARD_ECR_FIRST_SEASON} on and is published a few days before the board, so both know about the same offseason.
      </li>
      <li>
        The chance of a Cliff only means something for a player who plays {BOARD_MIN_GAMES} or more games; read it with the chance of missed
        time, never added to it.
      </li>
      <li>
        &quot;Where we disagree&quot; ranks the whole board. The record of past disagreements ranked the Cliff among the players who went on
        to play {BOARD_MIN_GAMES} or more games (known only afterwards), so its groups of {BOARD_DISAGREE_TOP} can differ a little from the
        markers.
      </li>
      <li>
        Contract status and offseason roster moves before the week-1 depth chart are not inputs (no dated source); the team shown is last
        season&apos;s.
      </li>
      <li>One season&apos;s board is small: its hits and misses swing a lot from season to season.</li>
      <li>
        {liveBoards
          ? `${liveBoards} live ${liveBoards === 1 ? "board has" : "boards have"} been published; every other board is reconstructed.`
          : "No board has been made live yet: every board here is reconstructed."}
      </li>
    </ul>
  );
}
