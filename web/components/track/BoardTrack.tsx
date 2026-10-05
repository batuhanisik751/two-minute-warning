import Link from "next/link";
import BoardCalibration from "@/components/board/BoardCalibration";
import DisagreementRecord from "@/components/board/DisagreementRecord";
import Term from "@/components/Term";
import { boardCalibration, boardCell, type BoardTrackRow } from "@/lib/board";
import { fmtInt, pct } from "@/lib/format";
import { topNOf } from "@/lib/track-record";
import { BOARD_MIN_GAMES, TRACK_INTERVAL_LEVEL } from "@/lib/method";
import { getBoardCalibration, getBoardDisagreement, getBoardGraded, getBoardIndex, getBoardLive, getBoardTrack } from "@/lib/queries/board";
import { H4, NotPublished, Panel, StatTiles, type Stat } from "./parts";

function tiles(rows: BoardTrackRow[], note: string): Stat[] {
  const defs = [
    { label: "Chance of a Cliff: PR-AUC, every board", variant: "cliff_main", model: "logit", metric: "pr_auc" },
    { label: "Chance of missed time: PR-AUC, every board", variant: "cliff_missed", model: "logit_simple", metric: "pr_auc" },
    { label: `Share of each board's top ${topNOf("p_at_10")} by the Cliff chance who had a Cliff (players with ${BOARD_MIN_GAMES}+ games)`, variant: "cliff_main", model: "logit", metric: "p_at_10" },
  ];
  const out: Stat[] = [];
  for (const d of defs) {
    const c = boardCell(rows, { variant: d.variant, slice: "all", model: d.model, metric: d.metric });
    if (!c) continue;
    const f = (x: number) => (d.metric === "p_at_10" ? pct(x, 1) : x.toFixed(3));
    out.push({
      label: d.label,
      term: d.metric === "pr_auc" ? { name: "pr_auc", text: "PR-AUC" } : undefined,
      value: f(c.value),
      interval: c.lo !== null && c.hi !== null ? `${pct(TRACK_INTERVAL_LEVEL)} interval ${f(c.lo)} to ${f(c.hi)}` : null,
      note,
    });
  }
  return out;
}

/** /track-record#board: the walk-forward backtest (reconstructed boards, final outcomes) and the
 *  live boards (outcomes pending until the season is over), kept apart. Breakout is not here: it is
 *  research only (/methodology#board-breakout). */
export default async function BoardTrack() {
  const [track, cal, record, live, graded, index] = await Promise.all([getBoardTrack(), getBoardCalibration(), getBoardDisagreement(), getBoardLive(), getBoardGraded(), getBoardIndex()]);
  const shown = track.filter((r) => !r.research);
  const stats = tiles(shown, graded.from !== null ? `${fmtInt(graded.boards)} boards, ${graded.from}–${graded.to}` : "");
  const groups = boardCalibration(cal);
  const newest = index[0];
  return (
    <>
      <Panel kind="backtest" title="The backtest" context="Cliff board" id="board-backtest-panel">
        {stats.length ? (
          <>
            <p>
              Every past board is estimated by models that learned only from the boards before it, at the eve of week 1 (the{" "}
              <Term name="as_of">as-of time</Term>), and compared with what really happened that season.
            </p>
            <StatTiles testId="board-headline" stats={stats} />
          </>
        ) : (
          <NotPublished what="The board's backtest" />
        )}
        <H4>Calibration by band</H4>
        {groups.length ? (
          <>
            <BoardCalibration groups={groups} idPrefix="track-board-cal" />
            <p id="track-board-cal-note" className="text-sm text-muted">
              Average estimate against how often it really happened; the Cliff among players with {BOARD_MIN_GAMES} or more games.
            </p>
          </>
        ) : (
          <NotPublished what="The board's calibration" />
        )}
        <H4>Against the experts</H4>
        <DisagreementRecord rows={record} variant="cliff_main" idPrefix="track" />
        <p className="text-sm">
          <Link href="/methodology#board">Both models compared, the Breakout research and the limits</Link>
        </p>
      </Panel>
      <Panel kind="live" title="Live boards" context="Cliff board" id="board-live">
        <div data-testid="board-live" data-live={live.boards ? (live.final ? "graded" : "pending") : "none"}>
          {live.final ? (
            <p>
              {fmtInt(live.cliffs)} Cliffs and {fmtInt(live.missed)} players with missed time among the {fmtInt(live.final)} graded players of{" "}
              {fmtInt(live.boards)} live {live.boards === 1 ? "board" : "boards"}. The rest are pending until their season is over.
            </p>
          ) : (
            <>
              <p className="font-semibold">No live results yet.</p>
              <p className="mt-1 text-sm text-muted">
                {live.boards
                  ? `${fmtInt(live.boards)} live ${live.boards === 1 ? "board" : "boards"} (${fmtInt(live.rows)} players): outcomes pending until the season is over. Nothing is graded before then.`
                  : `${newest ? `No board has been made live yet: the newest (${newest.season}) was reconstructed after its as-of. ` : "No board has been made live yet. "}Live outcomes will stay pending until each season is over.`}
              </p>
            </>
          )}
        </div>
      </Panel>
    </>
  );
}
