import WeekPicker from "@/components/WeekPicker";
import { NotCovered } from "@/components/time-machine/ModuleSection";
import { RadarSection, RegressionSection, StreamSection } from "@/components/time-machine/ListSections";
import { BoardSection, DecisionsSection, HotSeatSection } from "@/components/time-machine/OtherSections";
import Term from "@/components/Term";
import { EmptyState, Note, PageHeader } from "@/components/ui";
import { parseInt4, type WeekRef } from "@/lib/params";
import { sortPositions } from "@/lib/positions";
import { getBoardIndex } from "@/lib/queries/board";
import { getHotSeatIndex } from "@/lib/queries/hot-seat";
import { getListIndex } from "@/lib/queries/radar";
import { getRegressionIndex } from "@/lib/queries/regression";
import { getStreamIndex, getStreamPositions } from "@/lib/queries/stream";
import { getDecisionWeeks } from "@/lib/queries/time-machine";
import {
  MODULES,
  PRESEASON_WEEK,
  calendar,
  chooseWeek,
  kindAt,
  notCovered,
  parseWeek,
  pickerIndex,
  timeMachineHref,
  weekNote,
  weekTitle,
  whenName,
  type Covered,
  type ModuleId,
} from "@/lib/time-machine";
import { pageMetadata } from "@/lib/seo";
import { docUrl } from "@/lib/site";

export const metadata = pageMetadata("/time-machine", "Time machine", "The time machine: pick a published week and see what every module said then, from the stored lists, with what happened afterwards.");

const REPRO_URL = docUrl("docs/timemachine.md");

function Intro() {
  return (
    <PageHeader title="Time machine" kicker="What each module said then">
      Pick a season and a week to see what every module said at the time, with what happened afterwards. A{" "}
      <Term name="list_kind">live</Term> list was made in real time and never changed; a reconstructed one is what the module would
      have said then, rebuilt later from the data public that week (a backtest). The Decision Report Card grades calls after the games.
      Nothing here is recomputed: these are the stored lists, exactly as published (a separate{" "}
      <a href={REPRO_URL}>reproducibility check</a> recomputes them and compares).
    </PageHeader>
  );
}

type Kind = "live" | "backtest";
const keyed = (module: ModuleId, rows: readonly { season: number; week: number; kind: Kind }[]): Covered[] => rows.map((r) => ({ module, season: r.season, week: r.week, kind: r.kind }));

export default async function TimeMachinePage({ searchParams }: PageProps<"/time-machine">) {
  const sp = await searchParams;
  const [radar, streamPos, regression, hotSeat, board, decisions] = await Promise.all([
    getListIndex(),
    getStreamPositions(),
    getRegressionIndex(),
    getHotSeatIndex(),
    getBoardIndex(),
    getDecisionWeeks(),
  ]);
  const streamPositions = sortPositions(streamPos);
  const streams = await Promise.all(streamPositions.map((p) => getStreamIndex(p)));
  const covered: Covered[] = [
    ...keyed("radar", radar),
    ...streams.flatMap((s) => keyed("stream", s)),
    ...keyed("regression", regression),
    ...decisions.map((w) => ({ module: "decisions" as const, season: w.season, week: w.week, kind: null })),
    ...keyed("hot_seat", hotSeat),
    ...keyed("board", board),
  ];
  const cal = calendar(covered);
  const askedSeason = parseInt4(sp.season);
  const askedWeek = parseWeek(sp.week);
  const { chosen, exact } = chooseWeek(cal, askedSeason, askedWeek);
  if (!chosen) {
    return (
      <>
        <Intro />
        <EmptyState title="Nothing published yet">
          <p>The time machine fills up after the first publish: every module&apos;s lists appear here, week by week.</p>
        </EmptyState>
      </>
    );
  }
  const at: WeekRef = { season: chosen.season, week: chosen.week };
  const byWeek = new Map(cal.map((w) => [`${w.season}-${w.week}`, w]));
  const streamLists = streamPositions.map((position, i) => ({ position, kind: kindAt(streams[i], at) })).filter((l): l is { position: string; kind: Kind } => l.kind !== null);
  const radarKind = kindAt(radar, at);
  const regressionKind = kindAt(regression, at);
  const hotSeatKind = kindAt(hotSeat, at);
  const boardKind = kindAt(board, at);
  const graded = decisions.some((w) => w.season === at.season && w.week === at.week);
  const sections: Record<ModuleId, React.ReactNode> = {
    radar: radarKind ? <RadarSection at={at} kind={radarKind} /> : null,
    stream: streamLists.length ? <StreamSection at={at} lists={streamLists} /> : null,
    regression: regressionKind ? <RegressionSection at={at} kind={regressionKind} /> : null,
    decisions: graded ? <DecisionsSection at={at} /> : null,
    hot_seat: hotSeatKind ? <HotSeatSection at={at} kind={hotSeatKind} /> : null,
    board: boardKind ? <BoardSection at={at} kind={boardKind} /> : null,
  };
  const missing = MODULES.filter((m) => sections[m] === null);
  const asked = askedSeason !== null || askedWeek !== null;
  return (
    <>
      <Intro />
      <WeekPicker
        index={pickerIndex(cal)}
        chosen={{ ...at, kind: chosen.kinds[0] ?? "backtest" }}
        action="/time-machine"
        hidden={{}}
        hrefFor={(w) => timeMachineHref(w)}
        weekName={(w) => (w.week === PRESEASON_WEEK ? weekTitle(w.week) : null)}
        weekNote={(w) => {
          const c = byWeek.get(`${w.season}-${w.week}`);
          return c ? weekNote(c) : null;
        }}
        submit="Show week"
      />
      {asked && !exact ? (
        <div className="mt-4">
          <Note tone="warn">Nothing was published for that week; showing {whenName(at)} instead.</Note>
        </div>
      ) : null}
      <p className="mt-6 text-sm text-muted" data-testid="tm-coverage">
        {whenName(at)}: {chosen.modules.length} of the {MODULES.length} modules {chosen.modules.length === 1 ? "has" : "have"} something stored for this week
        {missing.length ? "; the others are listed at the bottom" : ""}.
      </p>
      {MODULES.map((m) => (sections[m] ? <div key={m}>{sections[m]}</div> : null))}
      {missing.length ? (
        <div className="mt-10" data-testid="tm-not-covered">
          {missing.map((m) => {
            const n = notCovered(m, covered.filter((c) => c.module === m), at);
            return <NotCovered key={m} module={m} at={at} reason={n.reason} see={n.see} seeHref={n.see ? timeMachineHref(n.see) : null} />;
          })}
        </div>
      ) : null}
    </>
  );
}
