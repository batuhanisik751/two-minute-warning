import Link from "next/link";
import PickList from "@/components/PickList";
import Term from "@/components/Term";
import { EmptyState, KindBadge, Note, PageHeader } from "@/components/ui";
import { fmtUtc, pct, pctRange, seasonWeek } from "@/lib/format";
import { AS_OF_TIME_UTC, AS_OF_WEEKDAY, POSITION_NAMES, TRACK_INTERVAL_LEVEL, positionOrder } from "@/lib/method";
import { getSiteMeta } from "@/lib/queries/meta";
import { getLatestLiveTop, getListIndex, getRadarModel } from "@/lib/queries/radar";
import { getTrackRows } from "@/lib/queries/track";
import { headline } from "@/lib/track";

export const metadata = { title: "This week" };

const TOP = 5;

async function TrackHeadline() {
  const model = await getRadarModel();
  if (!model) return null;
  const h = headline(await getTrackRows(["pooled", "diff"]), model);
  if (!h || h.radar.value === null || h.baseline.value === null) return null;
  const range = `${h.range.seasonFrom}–${h.range.seasonTo}`;
  return (
    <section aria-labelledby="track-heading" className="mb-8 rounded-lg border border-line bg-surface p-4">
      <h2 id="track-heading" className="text-sm font-semibold uppercase tracking-wide text-muted">
        Track record
      </h2>
      <p className="mt-1 text-lg" data-testid="track-headline">
        In the {range} <Term name="walk_forward">backtest</Term>, on average{" "}
        <strong className="tnum">{pct(h.radar.value, 1)}</strong> of the Radar&apos;s weekly top 10 were{" "}
        <Term name="y_hit" showFormula>
          hits
        </Term>
        {h.radar.low !== null && h.radar.high !== null ? (
          <>
            {" "}
            ({pct(TRACK_INTERVAL_LEVEL)} <Term name="interval">interval</Term>{" "}
            <span className="tnum">{pctRange(h.radar.low, h.radar.high, 1)}</span>)
          </>
        ) : null}
        , against <strong className="tnum">{pct(h.baseline.value, 1)}</strong> for a list of last week&apos;s top
        scorers (<Term name="precision_at_10">precision@10</Term>).
      </p>
      <p className="mt-2 text-sm">
        <Link href="/methodology#results">All results on the Methodology page</Link>
      </p>
    </section>
  );
}

function RegressionCard() {
  return (
    <section aria-labelledby="regression-card" className="rounded-lg border border-dashed border-line bg-surface p-4">
      <h2 id="regression-card" className="text-lg font-semibold">
        Regression Watch arrives in a later phase
      </h2>
      <p className="mt-1 text-muted">
        It will separate each player&apos;s scoring into opportunity and efficiency, and flag hot streaks that
        are unlikely to last. Nothing is published for it yet. <Link href="/regression">What it will do</Link>
      </p>
    </section>
  );
}

export default async function HomePage() {
  const [meta, live, index] = await Promise.all([getSiteMeta(), getLatestLiveTop(TOP), getListIndex()]);
  const lists = live ? [...live.lists].sort((a, b) => positionOrder(a.header.position) - positionOrder(b.header.position)) : [];
  const latestRecon = index.find((r) => r.kind === "backtest" && r.season === meta.currentSeason) ?? null;
  const stale =
    live && meta.current && (live.week.season !== meta.current.season || live.week.week !== meta.current.week);

  return (
    <>
      <PageHeader title="This week">
        The Waiver Radar ranks players who are probably still on waivers (the{" "}
        <Term name="candidate_pool">candidate pool</Term>) by their <Term name="chance">chance</Term> of becoming a
        fantasy starter soon. Below: the top {TOP} of each position&apos;s newest live list.
      </PageHeader>

      <TrackHeadline />

      {live && lists.length ? (
        <section aria-labelledby="live-heading" className="mb-10">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <h2 id="live-heading" className="text-xl font-semibold">
              Waiver Radar, {seasonWeek(live.week.season, live.week.week)}
            </h2>
            <KindBadge kind="live" />
          </div>
          <p className="mb-4 text-sm text-muted">
            Made at the <Term name="as_of">as-of time</Term> {fmtUtc(lists[0].header.asOf)} from the data public
            then. <Term name="list_kind">Live</Term> lists are never changed afterwards.
          </p>
          {stale && meta.current ? (
            <div className="mb-4">
              <Note tone="warn">
                There is no live list for {seasonWeek(meta.current.season, meta.current.week)} yet; this is the newest
                one.
              </Note>
            </div>
          ) : null}
          <div className="grid gap-6 lg:grid-cols-2">
            {lists.map((l) => (
              <section key={l.header.position} aria-labelledby={`pos-${l.header.position}`} className="frame-live rounded-lg p-3">
                <h3 id={`pos-${l.header.position}`} className="mb-2 text-lg font-semibold">
                  {POSITION_NAMES[l.header.position] ?? l.header.position} ({l.header.position})
                </h3>
                {l.header.note ? <p className="mb-2 text-sm font-medium">{l.header.note}</p> : null}
                {l.picks.length ? (
                  <PickList
                    picks={l.picks}
                    position={l.header.position}
                    label={`${l.header.position} top ${TOP}, ${seasonWeek(l.header.season, l.header.week)}, live`}
                    reasons={false}
                  />
                ) : (
                  <p className="text-muted">This list has no players.</p>
                )}
                <p className="mt-2 text-sm">
                  <Link
                    href={`/waivers?pos=${l.header.position}&season=${l.header.season}&week=${l.header.week}&kind=live`}
                  >
                    Full {l.header.position} list with reasons and outcomes
                  </Link>
                </p>
              </section>
            ))}
          </div>
        </section>
      ) : (
        <section aria-labelledby="live-heading" className="mb-10">
          <h2 id="live-heading" className="sr-only">
            Waiver Radar
          </h2>
          <EmptyState title="No live list yet">
            <p>
              The first live list appears on a {AS_OF_WEEKDAY} after the as-of time ({AS_OF_TIME_UTC} UTC), once
              that week&apos;s games and data have arrived. A live list is made in real time and never changed
              afterwards.
            </p>
            {latestRecon ? (
              <p>
                Meanwhile, the newest reconstructed list ({seasonWeek(latestRecon.season, latestRecon.week)}: what
                the Radar would have said that Tuesday) is on the{" "}
                <Link href={`/waivers?season=${latestRecon.season}&week=${latestRecon.week}&kind=backtest`}>
                  Waivers page
                </Link>
                .
              </p>
            ) : (
              <p>
                Past seasons&apos; reconstructed lists are on the <Link href="/waivers">Waivers page</Link>.
              </p>
            )}
          </EmptyState>
        </section>
      )}

      <RegressionCard />
    </>
  );
}
