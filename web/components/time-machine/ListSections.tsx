import Link from "next/link";
import PickList from "@/components/PickList";
import RegressionList from "@/components/RegressionList";
import StreamList from "@/components/StreamList";
import Term from "@/components/Term";
import { PosBadge } from "@/components/ui";
import { kindLabel } from "@/lib/format";
import { waiversHref, type WeekRef } from "@/lib/params";
import { positionLabel, positionShort, sortPositions } from "@/lib/positions";
import { getGlossary } from "@/lib/queries/glossary";
import { getList, getPositions } from "@/lib/queries/radar";
import { getRegressionList } from "@/lib/queries/regression";
import { getStreamList } from "@/lib/queries/stream";
import { regressionHref, tagRows, tagTitle, TAGS } from "@/lib/regression";
import { streamTopN } from "@/lib/streamer";
import { whenName } from "@/lib/time-machine";
import { ModuleSection } from "./ModuleSection";

type Kind = "live" | "backtest";
const TOP = 5;
const kindWord = (k: Kind) => (k === "live" ? "live" : "reconstructed");

/** One position's top of a list, as on the home page: the badge, the name, the list, its link. */
function PositionCard({ pos, id, href, children }: { pos: string; id: string; href: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} data-pos={pos} className="pos-edge min-w-0 rounded-lg border border-line bg-surface/50 p-3">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <PosBadge pos={pos} />
        <h3 id={id} className="display text-xl uppercase">
          {positionLabel(pos)} ({positionShort(pos)})
        </h3>
      </div>
      {children}
      <p className="mt-2 text-sm">
        <Link href={href}>Full {positionShort(pos)} list with reasons</Link>
      </p>
    </section>
  );
}

/** The Waiver Radar's lists of that week: each position's top 5 with what happened. */
export async function RadarSection({ at, kind }: { at: WeekRef; kind: Kind }) {
  const positions = sortPositions(await getPositions());
  const lists = (await Promise.all(positions.map((p) => getList(at.season, at.week, p, kind)))).filter((l) => l !== null);
  return (
    <ModuleSection
      module="radar"
      at={at}
      kind={kind}
      kindLine={<><Term name="list_kind">{kindLabel(kind).long}</Term>. Each position&apos;s top {TOP}, with the <Term name="y_hit">outcome</Term>.</>}
      href={waiversHref({ season: at.season, week: at.week, kind })}
      linkText="Every list of this week on the Waivers page"
    >
      <div className="mt-4 grid gap-5 lg:grid-cols-2">
        {lists.map(({ header: h, picks }) => (
          <PositionCard key={h.position} pos={h.position} id={`tm-radar-${h.position}`} href={waiversHref({ pos: h.position, season: at.season, week: at.week, kind })}>
            {picks.length ? (
              <PickList picks={picks.slice(0, TOP)} label={`${h.position} top ${TOP}, ${whenName(at)}, ${kindWord(kind)}`} reasons={false} />
            ) : (
              <p className="text-muted">This list has no players.</p>
            )}
          </PositionCard>
        ))}
      </div>
    </ModuleSection>
  );
}

/** The streamer's K and D/ST lists of that week (each position with its own kind). */
export async function StreamSection({ at, lists: wanted }: { at: WeekRef; lists: { position: string; kind: Kind }[] }) {
  const [found, gloss] = await Promise.all([Promise.all(wanted.map((w) => getStreamList(at.season, at.week, w.position, w.kind))), getGlossary()]);
  const lists = found.filter((l) => l !== null);
  const topN = streamTopN(gloss);
  const kind: Kind = wanted.some((w) => w.kind === "live") ? "live" : "backtest";
  return (
    <ModuleSection
      module="stream"
      at={at}
      kind={kind}
      kindLine={<><Term name="list_kind">{kindLabel(kind, "the streamer").long}</Term>. Kickers and team defenses for next week&apos;s game: the top {TOP} of each, with the outcome.</>}
      href={waiversHref({ pos: wanted[0].position, season: at.season, week: at.week, kind: wanted[0].kind })}
      linkText="The streamer's lists on the Waivers page"
    >
      <div className="mt-4 grid gap-5 lg:grid-cols-2">
        {lists.map(({ header: h, picks }) => (
          <PositionCard key={h.position} pos={h.position} id={`tm-stream-${h.position}`} href={waiversHref({ pos: h.position, season: at.season, week: at.week, kind: h.kind })}>
            {picks.length ? (
              <StreamList picks={picks.slice(0, TOP)} topN={topN} reasons={false} label={`${positionShort(h.position)} top ${TOP}, ${whenName(at)}, ${kindWord(h.kind)}`} />
            ) : (
              <p className="text-muted">This list has no picks.</p>
            )}
          </PositionCard>
        ))}
      </div>
    </ModuleSection>
  );
}

/** Regression Watch's list of that week: the top 5 Sell-high and Buy-low, with what they scored
 *  over the rest of the season once it is over. */
export async function RegressionSection({ at, kind }: { at: WeekRef; kind: Kind }) {
  const data = await getRegressionList(at.season, at.week, kind);
  const rows = data?.rows ?? [];
  return (
    <ModuleSection
      module="regression"
      at={at}
      kind={kind}
      kindLine={<Term name="list_kind">{kindLabel(kind, "Regression Watch").long}</Term>}
      href={regressionHref({ season: at.season, week: at.week, kind })}
      linkText="Every tag with reasons, the chart and the track record"
    >
      {rows.length && rows.every((r) => r.outcome?.status !== "final") ? (
        <p className="mt-3 text-sm text-muted" data-testid="rw-pending">
          What happened: pending. What these players score over the rest of the season appears under each of them once the regular season is over.
        </p>
      ) : null}
      <div className="mt-4 grid gap-5 lg:grid-cols-2">
        {TAGS.map((t) => {
          const list = tagRows(rows, t);
          return (
            <section key={t} aria-labelledby={`tm-tag-${t}`} className="min-w-0">
              <h3 id={`tm-tag-${t}`} className="display mb-2 text-xl uppercase">
                <Term name={t}>{tagTitle(t)}</Term>{" "}
                <span className="text-base text-muted">
                  (top {Math.min(TOP, list.length)} of {list.length})
                </span>
              </h3>
              {list.length ? (
                <RegressionList rows={list.slice(0, TOP)} withGarbage reasons={false} label={`${tagTitle(t)} top ${TOP}, ${whenName(at)}, ${kindWord(kind)}`} />
              ) : (
                <p className="text-muted">No {tagTitle(t)} player this week.</p>
              )}
            </section>
          );
        })}
      </div>
    </ModuleSection>
  );
}
