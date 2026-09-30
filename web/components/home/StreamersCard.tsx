import Link from "next/link";
import StreamList from "@/components/StreamList";
import Term from "@/components/Term";
import { KindBadge, PosBadge } from "@/components/ui";
import { seasonWeek } from "@/lib/format";
import { positionLabel, positionShort, sortPositions } from "@/lib/positions";
import { getGlossary } from "@/lib/queries/glossary";
import { getLatestStreamLive } from "@/lib/queries/stream";
import { starterWeek, streamTopN } from "@/lib/streamer";
import { waiversHref } from "@/lib/params";

const TOP = 3;

/** The home page's Streamers card: the newest live K and D/ST lists' top 3 (else: none yet). */
export default async function StreamersCard() {
  const [live, gloss] = await Promise.all([getLatestStreamLive(), getGlossary()]);
  const topN = streamTopN(gloss);
  const order = sortPositions(live?.lists.map((l) => l.header.position) ?? []);
  const lists = order.map((p) => live!.lists.find((l) => l.header.position === p)!);
  return (
    <section aria-labelledby="streamers-card" className="min-w-0 rounded-xl border border-line bg-surface p-4 sm:p-5" data-testid="streamers-card">
      <p className="kicker">Kickers and team defenses</p>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <h2 id="streamers-card" className="section-title">
          Streamers{live ? `, ${seasonWeek(live.week.season, live.week.week)}` : ""}
        </h2>
        {live ? <KindBadge kind="live" /> : null}
      </div>
      {live && lists.length ? (
        <>
          <p className="mt-2 mb-4 text-sm text-muted">
            A one-week pick for next week&apos;s game: the top {TOP} by their <Term name="stream_chance">chance</Term> of a{" "}
            {lists.map((l) => `${starterWeek(topN, l.header.position).replace(" week", "")} ${positionShort(l.header.position)}`).join(" or ")} week.
          </p>
          <div className="grid gap-5">
            {lists.map((l) => {
              const pos = l.header.position;
              return (
                <section key={pos} aria-labelledby={`stream-${pos}`} data-pos={pos} className="pos-edge min-w-0 rounded-lg border border-line bg-surface/50 p-3">
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <PosBadge pos={pos} />
                    <h3 id={`stream-${pos}`} className="display text-xl uppercase">
                      {positionLabel(pos)}
                    </h3>
                  </div>
                  {l.picks.length ? (
                    <StreamList picks={l.picks.slice(0, TOP)} topN={topN} reasons={false} showOutcome={false} label={`${positionShort(pos)} top ${TOP}, ${seasonWeek(l.header.season, l.header.week)}, live`} />
                  ) : (
                    <p className="text-muted">This list has no picks.</p>
                  )}
                  <p className="mt-2 text-sm">
                    <Link href={waiversHref({ pos, season: l.header.season, week: l.header.week, kind: "live" })}>
                      Full {positionShort(pos)} list with reasons
                    </Link>
                  </p>
                </section>
              );
            })}
          </div>
        </>
      ) : (
        <p className="mt-2 text-muted">
          No live kicker or D/ST list yet: the first one appears after a week&apos;s games and data have arrived. Past
          seasons&apos; reconstructed lists are on the <Link href="/waivers">Waivers page</Link> once published.
        </p>
      )}
    </section>
  );
}
