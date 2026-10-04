import type { Metadata } from "next";
import Link from "next/link";
import BoardList from "@/components/board/BoardList";
import HowToRead from "@/components/board/HowToRead";
import SeasonPicker from "@/components/decisions/SeasonPicker";
import Term from "@/components/Term";
import ThirdPartyNote from "@/components/ThirdPartyNote";
import { EmptyState, KindBadge, Note, PageHeader } from "@/components/ui";
import { BoardWhatHappened } from "@/components/board/parts";
import { boardHref, boardKindWords, boardTally, disagreements, ecrSeason, hasEcr } from "@/lib/board";
import { fmtInt, fmtUtc } from "@/lib/format";
import { BOARD_CLIFF_DROP, BOARD_MIN_GAMES, BOARD_MIN_PRIOR, BOARD_TOP_PPG } from "@/lib/method";
import { parseInt4, parseKind } from "@/lib/params";
import { positionShort, sortPositions } from "@/lib/positions";
import { getBoard, getBoardCalibration, getBoardDisagreement, getBoardIndex, type BoardEntry, type BoardHeader, type BoardKey } from "@/lib/queries/board";
import { pageMetadata } from "@/lib/seo";
import { showThirdPartyRanks } from "@/lib/third-party";

const DESCRIPTION = "The Cliff board: veterans' estimated chance of a fantasy cliff next season and, separately, of missed time";

/** Per request: the experts' ranks are named only where they are shown (lib/third-party.ts). */
export function generateMetadata(): Metadata {
  return pageMetadata("/board", "Cliff board", `${DESCRIPTION}${showThirdPartyRanks() ? ", next to the experts' preseason ranks" : ""}.`);
}

function Intro() {
  return (
    <PageHeader title="Cliff board" kicker="Veterans, next season, estimated">
      For established veterans ({BOARD_MIN_PRIOR}+ seasons in the league, top {BOARD_TOP_PPG} at their position in points per game), two
      estimated chances for the coming season: a drop of {Math.round(BOARD_CLIFF_DROP * 100)}% or more in points per game (a Cliff), and
      playing fewer than {BOARD_MIN_GAMES} games (missed time){showThirdPartyRanks() ? ", beside the experts' preseason ranks" : ""}. Estimates from past seasons&apos;
      patterns, not verdicts; how to read them is below the board.
    </PageHeader>
  );
}

/** The newest season's board, live first; an asked season (and kind) when it exists. */
function choose(index: BoardKey[], season: number | null, kind: "live" | "backtest" | null): { chosen: BoardKey | null; exact: boolean } {
  if (season === null) return { chosen: index[0] ?? null, exact: true };
  const same = index.filter((r) => r.season === season);
  const hit = same.find((r) => kind === null || r.kind === kind) ?? same[0];
  return hit ? { chosen: hit, exact: kind === null || hit.kind === kind } : { chosen: index[0] ?? null, exact: false };
}

export default async function BoardPage({ searchParams }: PageProps<"/board">) {
  const sp = await searchParams;
  const [index, cal, record] = await Promise.all([getBoardIndex(), getBoardCalibration(), getBoardDisagreement()]);
  const asked = parseInt4(sp.season);
  const { chosen, exact } = choose(index, asked, parseKind(sp.kind));
  if (!chosen) {
    return (
      <>
        <Intro />
        <EmptyState title="No board published yet">
          <p>The board appears here after the first publish that includes it.</p>
        </EmptyState>
        <HowToRead cal={cal} record={record} />
      </>
    );
  }
  const data = await getBoard(chosen.season, chosen.kind);
  const positions = sortPositions(new Set((data?.rows ?? []).map((r) => r.position)));
  const rawPos = typeof sp.pos === "string" ? sp.pos.toUpperCase() : null;
  const pos = rawPos && positions.includes(rawPos) ? rawPos : null;
  const seasons = [...new Set(index.map((r) => r.season))];
  const other = index.find((r) => r.season === chosen.season && r.kind !== chosen.kind);
  return (
    <>
      <Intro />
      <SeasonPicker
        seasons={seasons}
        chosen={chosen.season}
        action="/board"
        hidden={pos ? { pos } : {}}
        hrefFor={(s) => boardHref({ season: s, pos: pos ?? undefined })}
      />
      {(asked !== null && !exact) ? (
        <div className="mt-4">
          <Note tone="warn">There is no such board; showing the {chosen.season === index[0]?.season ? "newest" : "nearest"} one instead.</Note>
        </div>
      ) : null}
      <PositionFilter positions={positions} current={pos} season={chosen.season} kind={chosen.kind} />
      <section
        aria-labelledby="board-heading"
        className={`mt-4 rounded-xl bg-surface/40 p-3 sm:p-4 ${chosen.kind === "live" ? "frame-live" : "frame-recon"}`}
        data-testid="board"
        data-kind={chosen.kind}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-1">
          <h2 id="board-heading" className="display min-w-0 text-2xl uppercase sm:text-3xl">
            Cliff board {chosen.season}
            {pos ? `, ${positionShort(pos)}` : ""}
          </h2>
          <KindBadge kind={chosen.kind} />
        </div>
        <p className="mt-1 text-sm" data-testid="kind-label">
          <Term name="board_kind">{boardKindWords(chosen.kind)}</Term>.
        </p>
        {data ? <Body header={data.header} rows={data.rows} pos={pos} /> : <EmptyState title="This board could not be read" />}
        {other ? (
          <p className="mt-4 text-sm">
            This season also has a {other.kind === "live" ? "live" : "reconstructed"} board:{" "}
            <Link href={boardHref({ season: chosen.season, kind: other.kind, pos: pos ?? undefined })}>show it</Link>.
          </p>
        ) : null}
      </section>
      <HowToRead cal={cal} record={record} />
      <p className="mt-8 text-sm">
        <Link href="/methodology#board">How the board works</Link> · <Link href="/track-record#board">its track record</Link>
      </p>
    </>
  );
}

function PositionFilter({ positions, current, season, kind }: { positions: string[]; current: string | null; season: number; kind: string }) {
  if (positions.length < 2) return null;
  const tabs: [string | null, string][] = [[null, "All"], ...positions.map((p): [string, string] => [p, positionShort(p)])];
  return (
    <nav aria-label="Position" className="mt-5">
      <ul className="flex flex-wrap gap-1.5 sm:gap-2">
        {tabs.map(([p, label]) => (
          <li key={label}>
            <Link
              href={boardHref({ season, kind, pos: p ?? undefined })}
              aria-current={p === current ? "page" : undefined}
              data-pos={p ?? "ALL"}
              className="pos-tab inline-flex min-h-11 min-w-14 items-center justify-center rounded-md px-3 no-underline sm:min-w-16 sm:px-4"
            >
              {label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function Body({ header, rows, pos }: { header: BoardHeader; rows: BoardEntry[]; pos: string | null }) {
  const tally = boardTally(rows);
  const live = header.kind === "live";
  const ecr = hasEcr(rows);
  const marks = disagreements(rows);
  const shown = pos ? rows.filter((r) => r.position === pos) : rows;
  return (
    <>
      <p className="mt-2 text-sm text-muted">
        As of <time dateTime={header.asOf}>{fmtUtc(header.asOf)}</time> (the <Term name="as_of">as-of time</Term>); {live ? "made" : "reconstructed"} on{" "}
        <time dateTime={header.generatedAt}>{fmtUtc(header.generatedAt)}</time>. {fmtInt(header.nPlayers)} players, by the estimated chance of a
        Cliff; models <span className="font-mono text-xs break-all">{header.modelVersion}</span> and{" "}
        <span className="font-mono text-xs break-all">{header.missedVersion}</span>, trained on earlier seasons only.
      </p>
      <div className="mt-4 space-y-3">
        {header.incomplete ? <Note tone="warn">This board was made before all of its data had arrived (published as incomplete).</Note> : null}
        {header.note ? <Note>{header.note[0].toUpperCase() + header.note.slice(1)}.</Note> : null}
        {!showThirdPartyRanks() ? (
          <ThirdPartyNote />
        ) : !ecr ? (
          <Note>
            <span data-testid="no-ecr">
              {ecrSeason(header.season) ? "No experts' preseason ranks were published for this board." : "The experts' preseason ranks start later: this board has none."}
            </span>
          </Note>
        ) : null}
        <BoardWhatHappened tally={tally} />
      </div>
      <div className="mt-4">
        {shown.length ? (
          <BoardList rows={shown} marks={marks} ecr={ecr} label={`Cliff board ${header.season}${pos ? `, ${pos}` : ""}, ${live ? "live" : "reconstructed"}`} showOutcome={tally.final > 0} />
        ) : (
          <p className="text-muted">This board has no players at this position.</p>
        )}
      </div>
    </>
  );
}
