import Link from "next/link";
import HotSeatList from "@/components/hot-seat/HotSeatList";
import HowToRead from "@/components/hot-seat/HowToRead";
import Term from "@/components/Term";
import WeekPicker from "@/components/WeekPicker";
import { EmptyState, KindBadge, Note, PageHeader } from "@/components/ui";
import { fmtInt, fmtUtc, kindLabel } from "@/lib/format";
import { HotSeatWhatHappened } from "@/components/hot-seat/parts";
import { hotSeatHref, hotSeatTally, listName, mergeTimeline, type TimelinePoint } from "@/lib/hot-seat";
import { HOT_SEAT_WINDOW_DAYS } from "@/lib/method";
import { chooseList, parseInt4, parseKind, type ListKey, type WeekRef } from "@/lib/params";
import { getHotSeatCalibration, getHotSeatIndex, getHotSeatList, getHotSeatTimeline, type HotSeatEntry, type HotSeatHeader, type HotSeatKey } from "@/lib/queries/hot-seat";
import { pageMetadata } from "@/lib/seo";

export const metadata = pageMetadata("/hot-seat", "Hot-Seat Meter", "The Hot-Seat Meter: each NFL head coach's estimated chance of being let go, week by week, the drivers in words, and how past seasons' estimates turned out.");

const SUBJECT = "the Hot-Seat Meter";

function Intro() {
  return (
    <PageHeader title="Hot-Seat Meter" kicker="Head coaches, estimated">
      For every NFL head coach, the estimated chance of being let go by {HOT_SEAT_WINDOW_DAYS} days after the season (a
      firing or a mutual parting), from his team&apos;s results against what the betting market expected, its point
      differential, his tenure and the rest of the record so far. An estimate from past seasons&apos; patterns, not a
      verdict on anyone; how to read it is below the list.
    </PageHeader>
  );
}

/** No week asked for: the newest live list of the newest season, else the newest list. */
function defaultList(index: HotSeatKey[]): ListKey | null {
  const top = index[0];
  if (!top) return null;
  return index.find((r) => r.kind === "live" && r.season === top.season) ?? top;
}

export default async function HotSeatPage({ searchParams }: PageProps<"/hot-seat">) {
  const sp = await searchParams;
  const [index, cal] = await Promise.all([getHotSeatIndex(), getHotSeatCalibration()]);
  const askedSeason = parseInt4(sp.season);
  const askedWeek = parseInt4(sp.week);
  const askedKind = parseKind(sp.kind);
  const asked = askedSeason !== null || askedWeek !== null || askedKind !== null;
  const picked = asked ? chooseList(index, askedSeason, askedWeek, askedKind) : { chosen: defaultList(index), exact: true };
  const chosen = picked.chosen;
  if (!chosen) {
    return (
      <>
        <Intro />
        <EmptyState title="No Hot-Seat list published yet">
          <p>The weekly lists appear here after the first publish that includes the Hot-Seat Meter.</p>
        </EmptyState>
        <HowToRead cal={cal} />
      </>
    );
  }
  const [data, season] = await Promise.all([getHotSeatList(chosen.season, chosen.week, chosen.kind), getHotSeatTimeline(chosen.season)]);
  const timelines = mergeTimeline(season);
  const weeks = season.map((r) => r.week);
  const span: [number, number] = weeks.length ? [Math.min(...weeks), Math.max(...weeks)] : [chosen.week, chosen.week];
  const snapshots = new Map(index.map((r) => [`${r.season}-${r.week}`, r.snapshot]));
  const weekName = (w: WeekRef) => (snapshots.get(`${w.season}-${w.week}`) === "end_of_season" ? "End of season" : null);
  const other = index.find((r) => r.season === chosen.season && r.week === chosen.week && r.kind !== chosen.kind);
  const name = listName(chosen.season, chosen.week, snapshots.get(`${chosen.season}-${chosen.week}`) ?? "weekly");
  const k = kindLabel(chosen.kind, SUBJECT);
  return (
    <>
      <Intro />
      <WeekPicker
        index={index}
        chosen={chosen}
        action="/hot-seat"
        hidden={{}}
        hrefFor={(w) => hotSeatHref({ season: w.season, week: w.week })}
        weekName={weekName}
      />
      {asked && !picked.exact ? (
        <div className="mt-4">
          <Note tone="warn">There is no list for that week; showing the nearest published one instead.</Note>
        </div>
      ) : null}
      <section
        aria-labelledby="hs-heading"
        className={`mt-6 rounded-xl bg-surface/40 p-3 sm:p-4 ${chosen.kind === "live" ? "frame-live" : "frame-recon"}`}
        data-testid="hot-seat-week"
        data-kind={chosen.kind}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-1">
          <h2 id="hs-heading" className="display min-w-0 text-2xl uppercase sm:text-3xl">
            Hot-Seat Meter, {name}
          </h2>
          <KindBadge kind={chosen.kind} />
        </div>
        <p className="mt-1 text-sm" data-testid="kind-label">
          <Term name="list_kind">{k.long}</Term>.
        </p>
        {data ? <ListBody header={data.header} rows={data.rows} timelines={timelines} span={span} name={name} /> : <EmptyState title="This list could not be read" />}
        {other ? (
          <p className="mt-4 text-sm">
            This week also has a {other.kind === "live" ? "live" : "reconstructed"} list:{" "}
            <Link href={hotSeatHref({ season: chosen.season, week: chosen.week, kind: other.kind })}>show the {other.kind === "live" ? "live" : "reconstructed"} list</Link>.
          </p>
        ) : null}
      </section>
      <HowToRead cal={cal} />
      <p className="mt-8 text-sm">
        <Link href="/methodology#hot-seat">How the Hot-Seat Meter works</Link> ·{" "}
        <Link href="/track-record#hot-seat">its track record</Link>
      </p>
    </>
  );
}

function ListBody({ header, rows, timelines, span, name }: { header: HotSeatHeader; rows: HotSeatEntry[]; timelines: Map<string, TimelinePoint[]>; span: [number, number]; name: string }) {
  const tally = hotSeatTally(rows);
  const live = header.kind === "live";
  return (
    <>
      <p className="mt-2 text-sm text-muted">
        As of <time dateTime={header.asOf}>{fmtUtc(header.asOf)}</time> (the <Term name="as_of">as-of time</Term>
        {header.snapshot === "end_of_season" && !header.note ? "; each team's row is as of its own last regular-season game" : ""});{" "}
        {live ? "made" : "reconstructed"} on <time dateTime={header.generatedAt}>{fmtUtc(header.generatedAt)}</time>.{" "}
        {fmtInt(header.nCoaches)} head coaches, by estimated chance; model{" "}
        <span className="font-mono text-xs">{header.modelVersion}</span>, trained on earlier seasons only.
      </p>
      <div className="mt-4 space-y-3">
        {header.incomplete ? <Note tone="warn">This list was made before all of the week&apos;s data had arrived (published as incomplete).</Note> : null}
        {/* the publish's own note (the end-of-season snapshot says how it is timed): information, not a warning */}
        {header.note ? <Note>{header.note[0].toUpperCase() + header.note.slice(1)}.</Note> : null}
        <HotSeatWhatHappened tally={tally} />
      </div>
      <div className="mt-4">
        {rows.length ? (
          <HotSeatList rows={rows} timelines={timelines} weeks={span} label={`Hot-Seat Meter, ${name}, ${live ? "live" : "reconstructed"}`} showOutcome={tally.final > 0} />
        ) : (
          <p className="text-muted">This list has no coaches.</p>
        )}
      </div>
    </>
  );
}
