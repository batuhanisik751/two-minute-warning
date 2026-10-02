import Link from "next/link";
import BucketBadges from "@/components/BucketBadges";
import FlexNote from "@/components/FlexNote";
import PickList from "@/components/PickList";
import PositionTabs from "@/components/PositionTabs";
import StreamBody from "@/components/StreamBody";
import Term from "@/components/Term";
import WeekPicker from "@/components/WeekPicker";
import { EmptyState, KindBadge, Note, PageHeader, PosBadge } from "@/components/ui";
import { flexOrderOf, mergeFlex } from "@/lib/flex";
import { fmtInt, fmtUtc, kindLabel, pct, seasonWeek } from "@/lib/format";
import { leagueShape } from "@/lib/league";
import { CHANCE_RANGE_LEVEL, POSITIONS } from "@/lib/method";
import { chooseList, parseInt4, parseKind, parsePosition, waiversHref, type ListKey } from "@/lib/params";
import { FLEX, FLEX_POSITIONS, positionLabel, positionShort, sortPositions, tabPositions } from "@/lib/positions";
import { getStreamIndex, getStreamPositions } from "@/lib/queries/stream";
import { isStreamPosition } from "@/lib/streamer";
import { getGlossary } from "@/lib/queries/glossary";
import { getSiteMeta } from "@/lib/queries/meta";
import {
  getBucketCounts,
  getFlexBucketCounts,
  getList,
  getListIndex,
  getPositions,
  type ListHeader,
  type Pick,
} from "@/lib/queries/radar";
import { pageMetadata } from "@/lib/seo";

export const metadata = pageMetadata("/waivers", "Waivers", "The Waiver Radar: each week's pickups by position and FLEX with the chance of a starter week, its range, the reasons and what happened, plus the K and D/ST streamers.");

function listWord(n: number): string {
  return n === 1 ? "list" : "lists";
}

/** "As of ...; made on ... . Ranked from N players ...; the model learned from ..." */
function ListMeta({ headers, kind, shown, flex }: { headers: ListHeader[]; kind: "live" | "backtest"; shown: number; flex: boolean }) {
  const h = headers[0];
  const training = h.trainingSeasons;
  const pool = headers.reduce((s, x) => s + x.nPool, 0);
  return (
    <p className="mt-2 text-sm text-muted">
      As of <time dateTime={h.asOf}>{fmtUtc(h.asOf)}</time> (the <Term name="as_of">as-of time</Term>);{" "}
      {kind === "live" ? "made" : "reconstructed"} on <time dateTime={h.generatedAt}>{fmtUtc(h.generatedAt)}</time>.{" "}
      {flex ? (
        <>
          Merged from the {headers.map((x) => x.position).join(", ").replace(/, ([^,]+)$/, " and $1")} {listWord(headers.length)} (
          {headers.map((x) => `${fmtInt(x.nPool)} ${x.position}`).join(", ")} players in their pools); the top {shown} are
          shown.
        </>
      ) : (
        <>
          Ranked from {fmtInt(pool)} players in the pool; the top {shown} are shown.
        </>
      )}
      {training.length ? (
        <>
          {" "}
          The model learned from {Math.min(...training)}
          {training.length > 1 ? `–${Math.max(...training)}` : ""} only.
        </>
      ) : null}
    </p>
  );
}

function ListNotes({ headers, picks, season, currentSeason, flex }: { headers: ListHeader[]; picks: Pick[]; season: number; currentSeason: number | null; flex: boolean }) {
  const notes = headers.filter((h) => h.note);
  return (
    <div className="mt-4 space-y-3">
      {headers.some((h) => h.incomplete) ? (
        <Note tone="warn">
          This list was made before all of the week&apos;s data had arrived (published as incomplete).
        </Note>
      ) : null}
      {notes.map((h) => (
        <Note key={h.position} tone="warn">
          {flex ? <strong>{h.position}: </strong> : null}
          {h.note}
        </Note>
      ))}
      {picks.length && picks.every((p) => p.chance === null) ? (
        <Note>
          <strong>Chance and priority: not available for this list.</strong> The chance is how often similar players hit
          in earlier seasons&apos; backtests; it was not computed for this reconstructed list, which shows the rank and
          what happened afterwards. The model&apos;s raw probability is not shown in its place.
        </Note>
      ) : null}
      {picks.length && picks.every((p) => p.reasons.length === 0) ? (
        <Note>
          <strong>No reasons for this list.</strong> Reasons are written by the weekly list; the walk-forward backtest
          that reconstructed this list did not generate them.
        </Note>
      ) : null}
      {season < (currentSeason ?? season) ? (
        <p className="text-sm text-muted">
          <Term name="current_franchise">Teams</Term> are shown by today&apos;s franchise code and name.
        </p>
      ) : null}
    </div>
  );
}

function Legend() {
  return (
    <p className="mt-4 text-sm text-muted">
      <Term name="chance">Chance</Term>: with the {pct(CHANCE_RANGE_LEVEL)} range of how often similar players hit (the
      bar is solid up to the chance and pale up to the top of the range). <Term name="priority">Priority</Term>: the
      suggested pickup priority.{" "}
      <Term name="y_hit" showFormula>
        Outcome
      </Term>
      : hit, no hit, or pending while the weeks after the list are still being played (W = week, then his weekly finish
      at the position).
    </p>
  );
}

export default async function WaiversPage({ searchParams }: PageProps<"/waivers">) {
  const sp = await searchParams;
  const [radarIndex, meta, present, streamPresent] = await Promise.all([getListIndex(), getSiteMeta(), getPositions(), getStreamPositions()]);
  const tabs = tabPositions([...present, ...streamPresent]);
  const position = parsePosition(sp.pos, tabs.length ? tabs : POSITIONS);
  const stream = isStreamPosition(position);
  // K and D/ST have their own weeks (the streamer's lists); the time machine follows them
  const index = stream ? await getStreamIndex(position) : radarIndex;
  const askedSeason = parseInt4(sp.season);
  const askedWeek = parseInt4(sp.week);
  const askedKind = parseKind(sp.kind);
  const { chosen, exact } = chooseList(index, askedSeason, askedWeek, askedKind);

  const intro = (
    <PageHeader title="Waiver Radar" kicker="The waiver wire">
      Each position&apos;s weekly list of players who are probably still on waivers (the{" "}
      <Term name="candidate_pool">candidate pool</Term>), ranked by their <Term name="chance">chance</Term> of becoming a
      fantasy starter soon. Pick a season and week to see what the Radar said then and what happened next.
      {streamPresent.length ? (
        <>
          {" "}
          The {sortPositions(streamPresent).map(positionShort).join(" and ")} tabs are different: a one-week pick for next week&apos;s game
          (streaming).
        </>
      ) : null}
    </PageHeader>
  );

  if (!chosen) {
    return (
      <>
        {intro}
        <EmptyState title="No lists published yet">
          <p>The Waiver Radar has not published any weekly list. Lists appear here after the first publish.</p>
        </EmptyState>
      </>
    );
  }

  const other = index.find((r) => r.season === chosen.season && r.week === chosen.week && r.kind !== chosen.kind);
  const k = kindLabel(chosen.kind, stream ? "the streamer" : "the Radar");
  const title = `${positionLabel(position)}${position === FLEX ? "" : ` (${positionShort(position)})`}, ${seasonWeek(chosen.season, chosen.week)}`;

  const asked =
    askedSeason !== null || askedWeek !== null || askedKind !== null
      ? [
          askedSeason !== null ? `${askedSeason}` : null,
          askedWeek !== null ? `week ${askedWeek}` : null,
          askedKind === "live" ? "(live)" : askedKind === "backtest" ? "(reconstructed)" : null,
        ]
          .filter(Boolean)
          .join(" ")
      : null;

  return (
    <>
      {intro}
      <PositionTabs positions={tabs} current={position} chosen={chosen} />
      <WeekPicker index={index} chosen={chosen} position={position} />

      {!exact && asked ? (
        <div className="mt-4">
          <Note tone="warn">There is no list for {asked}; showing the nearest published one instead.</Note>
        </div>
      ) : null}

      <section
        aria-labelledby="list-heading"
        className={`mt-6 rounded-xl bg-surface/40 p-3 sm:p-4 ${chosen.kind === "live" ? "frame-live" : "frame-recon"}`}
        data-testid="radar-list"
        data-kind={chosen.kind}
        data-position={position}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-1">
          <PosBadge pos={position} className="text-base" />
          <h2 id="list-heading" className="display min-w-0 text-2xl uppercase sm:text-3xl">
            {title}
          </h2>
          <KindBadge kind={chosen.kind} />
        </div>
        <p className="mt-1 text-sm" data-testid="kind-label">
          <Term name="list_kind">{k.long}</Term>.
        </p>

        {stream ? (
          <StreamBody chosen={chosen} position={position} currentSeason={meta.currentSeason} />
        ) : position === FLEX ? (
          <FlexBody chosen={chosen} present={present} meta={meta} />
        ) : (
          <PositionBody chosen={chosen} position={position} meta={meta} />
        )}

        {other ? (
          <p className="mt-4 text-sm">
            This week also has a {other.kind === "live" ? "live" : "reconstructed"} list:{" "}
            <Link href={waiversHref({ pos: position, season: chosen.season, week: chosen.week, kind: other.kind })}>
              show the {other.kind === "live" ? "live" : "reconstructed"} {positionShort(position)} list
            </Link>
            .
          </p>
        ) : null}
      </section>
    </>
  );
}

type Meta = Awaited<ReturnType<typeof getSiteMeta>>;

async function PositionBody({ chosen, position, meta }: { chosen: ListKey; position: string; meta: Meta }) {
  const [data, counts] = await Promise.all([
    getList(chosen.season, chosen.week, position, chosen.kind),
    getBucketCounts(position, chosen.season),
  ]);
  const k = kindLabel(chosen.kind);
  if (!data) {
    return (
      <div className="mt-4">
        <EmptyState title={`No ${position} list for this week`}>
          <p>
            {seasonWeek(chosen.season, chosen.week)} has lists for other positions only. Choose another position above.
          </p>
        </EmptyState>
      </div>
    );
  }
  return (
    <>
      <ListMeta headers={[data.header]} kind={chosen.kind} shown={data.picks.length} flex={false} />
      <ListNotes headers={[data.header]} picks={data.picks} season={chosen.season} currentSeason={meta.currentSeason} flex={false} />
      <div className="mt-4">
        <BucketBadges counts={counts} position={position} beforeSeason={chosen.season} />
      </div>
      <Legend />
      <div className="mt-3">
        {data.picks.length ? (
          <PickList
            picks={data.picks}
            label={`${position} list, ${seasonWeek(chosen.season, chosen.week)}, ${k.short.toLowerCase()}`}
          />
        ) : (
          <EmptyState title="This list has no players" />
        )}
      </div>
    </>
  );
}

async function FlexBody({ chosen, present, meta }: { chosen: ListKey; present: string[]; meta: Meta }) {
  const positions = FLEX_POSITIONS.filter((p) => present.includes(p));
  const [lists, counts, gloss] = await Promise.all([
    Promise.all(positions.map((p) => getList(chosen.season, chosen.week, p, chosen.kind))),
    getFlexBucketCounts(chosen.season),
    getGlossary(),
  ]);
  const found = lists.filter((l): l is NonNullable<typeof l> => l !== null);
  const k = kindLabel(chosen.kind);
  if (!found.length) {
    return (
      <div className="mt-4">
        <EmptyState title="No FLEX list for this week">
          <p>
            {seasonWeek(chosen.season, chosen.week)} has no RB, WR or TE list to merge. Choose another position above.
          </p>
        </EmptyState>
      </div>
    );
  }
  const merged = mergeFlex(found.flatMap((l) => l.picks));
  const missing = FLEX_POSITIONS.filter((p) => !found.some((l) => l.header.position === p));
  const headers = found.map((l) => l.header);
  return (
    <>
      <ListMeta headers={headers} kind={chosen.kind} shown={merged.length} flex />
      <div className="mt-4">
        <FlexNote shape={leagueShape(gloss)} order={flexOrderOf(merged)} missing={missing} />
      </div>
      <ListNotes headers={headers} picks={merged} season={chosen.season} currentSeason={meta.currentSeason} flex />
      <div className="mt-4">
        <BucketBadges counts={counts} position={FLEX} beforeSeason={chosen.season} flex />
      </div>
      <Legend />
      <div className="mt-3">
        {merged.length ? (
          <PickList
            picks={merged}
            flex
            label={`FLEX list (RB, WR and TE merged), ${seasonWeek(chosen.season, chosen.week)}, ${k.short.toLowerCase()}`}
          />
        ) : (
          <EmptyState title="This list has no players" />
        )}
      </div>
    </>
  );
}
