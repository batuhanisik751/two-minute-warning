import BoardList from "@/components/board/BoardList";
import { BoardWhatHappened } from "@/components/board/parts";
import DecisionList from "@/components/decisions/DecisionList";
import { SeasonSummary } from "@/components/decisions/SeasonSummary";
import HotSeatList from "@/components/hot-seat/HotSeatList";
import { HotSeatWhatHappened } from "@/components/hot-seat/parts";
import Term from "@/components/Term";
import { boardHref, boardKindWords, boardTally, disagreements, hasEcr } from "@/lib/board";
import { kindLabel } from "@/lib/format";
import { hotSeatHref, hotSeatTally, listName } from "@/lib/hot-seat";
import type { WeekRef } from "@/lib/params";
import { getBoard } from "@/lib/queries/board";
import { getBestCalls, getDecisionsMeta, getWorstCalls } from "@/lib/queries/decisions";
import { getHotSeatList } from "@/lib/queries/hot-seat";
import { getWeekCalls } from "@/lib/queries/time-machine";
import { whenName } from "@/lib/time-machine";
import { ModuleSection } from "./ModuleSection";

type Kind = "live" | "backtest";
const TOP = 10;
const WORST = 5;
const BEST = 3;
const kindWord = (k: Kind) => (k === "live" ? "live" : "reconstructed");

/** The Decision Report Card's graded calls of that week: the league's totals (coach_week), the
 *  costliest clear calls and the best calls against convention. Retrospective by nature. */
export async function DecisionsSection({ at }: { at: WeekRef }) {
  const [calls, worst, best, meta] = await Promise.all([
    getWeekCalls(at.season, at.week),
    getWorstCalls(at.season, null, at.week, WORST),
    getBestCalls(at.season, null, at.week, BEST),
    getDecisionsMeta(),
  ]);
  const when = whenName(at);
  return (
    <ModuleSection
      module="decisions"
      at={at}
      kind={null}
      kindLine={
        <>
          <strong>Grades are retrospective:</strong> every call was priced after the game, by <Term name="own_wp">our win-probability model</Term>{" "}
          trained only on seasons before this one{meta.version ? <> (version <span className="font-mono text-xs break-all">{meta.version}</span>)</> : null}. They say
          what the numbers think of the call, not what anyone said that week.
        </>
      }
      href={`/decisions?season=${at.season}`}
      linkText={`The ${at.season} leaderboard, worst calls and clock cases`}
    >
      <div className="mt-4">
        <SeasonSummary name={when} league={calls} level={3} />
      </div>
      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <section aria-labelledby="tm-worst" className="min-w-0">
          <h3 id="tm-worst" className="display mb-2 text-xl uppercase">
            Costliest clear calls
          </h3>
          {worst.length ? (
            <DecisionList rows={worst} label={`Costliest clear calls, ${when}`} testId="tm-worst" value="lost" />
          ) : (
            <p className="text-muted">No clearly wrong call this week.</p>
          )}
        </section>
        <section aria-labelledby="tm-best" className="min-w-0">
          <h3 id="tm-best" className="display mb-2 text-xl uppercase">
            Best calls <Term name="against_convention">against convention</Term>
          </h3>
          {best.length ? (
            <DecisionList rows={best} label={`Best calls against convention, ${when}`} testId="tm-best" value="gain" />
          ) : (
            <p className="text-muted">No clear go call was taken this week.</p>
          )}
        </section>
      </div>
    </ModuleSection>
  );
}

/** The Hot-Seat Meter's list of that week (or the end-of-season snapshot): the top 10 coaches with
 *  what happened (lib/hot-seat hotSeatTally over the shown rows). */
export async function HotSeatSection({ at, kind }: { at: WeekRef; kind: Kind }) {
  const data = await getHotSeatList(at.season, at.week, kind);
  const rows = (data?.rows ?? []).slice(0, TOP);
  const tally = hotSeatTally(rows);
  const name = data ? listName(at.season, at.week, data.header.snapshot) : whenName(at);
  return (
    <ModuleSection
      module="hot_seat"
      at={at}
      kind={kind}
      kindLine={
        <>
          <Term name="list_kind">{kindLabel(kind, "the Hot-Seat Meter").long}</Term>
          {data?.header.snapshot === "end_of_season" ? " (the end-of-season snapshot)" : ""}. The {rows.length} head coaches with the highest{" "}
          <Term name="hot_seat_estimate">estimated chance</Term> of being let go: estimates, not verdicts.
        </>
      }
      href={hotSeatHref({ season: at.season, week: at.week, kind })}
      linkText="Every coach, the drivers and how to read it"
    >
      <div className="mt-4">
        <HotSeatWhatHappened tally={tally} />
      </div>
      <div className="mt-4">
        <HotSeatList rows={rows} timelines={new Map()} weeks={[at.week, at.week]} label={`Hot-Seat Meter top ${TOP}, ${name}, ${kindWord(kind)}`} showOutcome={tally.final > 0} compact />
      </div>
    </ModuleSection>
  );
}

/** The Cliff board of that season (its preseason week): the top 10 by the chance of a Cliff, with
 *  what happened (lib/board boardTally over the shown rows). */
export async function BoardSection({ at, kind }: { at: WeekRef; kind: Kind }) {
  const data = await getBoard(at.season, kind);
  const all = data?.rows ?? [];
  const rows = all.slice(0, TOP);
  const tally = boardTally(rows);
  return (
    <ModuleSection
      module="board"
      at={at}
      kind={kind}
      kindLine={
        <>
          <Term name="board_kind">{boardKindWords(kind)}</Term>. The {rows.length} veterans with the highest{" "}
          <Term name="board_cliff_chance">estimated chance of a Cliff</Term>, with the separate chance of missed time: estimates, not verdicts.
        </>
      }
      href={boardHref({ season: at.season, kind })}
      linkText="Every player, the experts' ranks, the drivers and how to read it"
    >
      <div className="mt-4">
        <BoardWhatHappened tally={tally} />
      </div>
      <div className="mt-4">
        <BoardList rows={rows} marks={disagreements(all)} ecr={hasEcr(all)} label={`Cliff board top ${TOP}, ${at.season}, ${kindWord(kind)}`} showOutcome={tally.final > 0} compact />
      </div>
    </ModuleSection>
  );
}
