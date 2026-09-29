import Link from "next/link";
import FlexNote from "@/components/FlexNote";
import PickList from "@/components/PickList";
import Term from "@/components/Term";
import TopBoard from "@/components/TopBoard";
import { EmptyState, KindBadge, Note, PosBadge } from "@/components/ui";
import { flexOrderOf, mergeFlex } from "@/lib/flex";
import { fmtUtc, pct, pctRange, seasonWeek } from "@/lib/format";
import { leagueShape } from "@/lib/league";
import { AS_OF_TIME_UTC, AS_OF_WEEKDAY, TRACK_INTERVAL_LEVEL } from "@/lib/method";
import { FLEX, FLEX_POSITIONS, displayOrder, positionLabel } from "@/lib/positions";
import { getGlossary } from "@/lib/queries/glossary";
import { getSiteMeta } from "@/lib/queries/meta";
import { getLatestLive, getListIndex, getRadarModel } from "@/lib/queries/radar";
import { getTrackRows } from "@/lib/queries/track";
import { headline } from "@/lib/track";

export const metadata = { title: "This week" };

const TOP = 5;

function StatBar({ label, value, radar }: { label: string; value: number; radar: boolean }) {
  return (
    <div className="rounded-lg border border-line bg-raised px-4 py-3">
      <span className="block font-display text-sm font-bold tracking-wider text-muted uppercase">{label}</span>
      <span className="big-number mt-1 block text-5xl">{pct(value, 1)}</span>
      <span className="meter mt-2 block">
        <span className={radar ? "meter-fill" : "meter-fill meter-neutral"} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
      </span>
    </div>
  );
}

async function TrackHeadline() {
  const model = await getRadarModel();
  if (!model) return null;
  const h = headline(await getTrackRows(["pooled", "diff"]), model);
  if (!h || h.radar.value === null || h.baseline.value === null) return null;
  const range = `${h.range.seasonFrom}–${h.range.seasonTo}`;
  return (
    <section aria-labelledby="track-heading" className="mb-10 rounded-xl border border-line bg-surface p-4 sm:p-5">
      <h2 id="track-heading" className="section-title">
        Track record
      </h2>
      {/* the sentence below says the same; the two numbers are its scoreboard */}
      <div aria-hidden="true" className="mt-3 grid gap-3 sm:grid-cols-2">
        <StatBar label="The Radar's top 10" value={h.radar.value} radar />
        <StatBar label="Last week's top scorers" value={h.baseline.value} radar={false} />
      </div>
      <p className="mt-3 text-lg" data-testid="track-headline">
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
    <section aria-labelledby="regression-card" className="rounded-xl border-2 border-dashed border-line bg-surface p-4 sm:p-5">
      <p className="kicker">Coming later</p>
      <h2 id="regression-card" className="section-title mt-1">
        Regression Watch arrives in a later phase
      </h2>
      <p className="mt-2 text-muted">
        It will separate each player&apos;s scoring into opportunity and efficiency, and flag hot streaks that are
        unlikely to last. Nothing is published for it yet. <Link href="/regression">What it will do</Link>
      </p>
    </section>
  );
}

/** The field banner: the page title, and (with a live list) the week on a scoreboard. */
function Banner({ week, asOf }: { week: { season: number; week: number } | null; asOf: string | null }) {
  return (
    <section aria-labelledby="page-title" className="field mb-8 rounded-xl">
      <div className="grid items-end gap-6 p-5 sm:p-8 md:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <p className="kicker">Waiver Radar</p>
          <h1 id="page-title" className="display mt-1 text-5xl font-extrabold uppercase sm:text-6xl">
            This week
          </h1>
          <p className="mt-3 max-w-2xl text-field-muted">
            The Waiver Radar ranks players who are probably still on waivers (the{" "}
            <Term name="candidate_pool">candidate pool</Term>) by their <Term name="chance">chance</Term> of becoming a
            fantasy starter soon. Below: the top {TOP} of each position&apos;s newest live list.
          </p>
        </div>
        {week ? (
          <div className="scoreboard rounded-lg p-4" data-testid="scoreboard">
            <dl className="grid grid-cols-2 divide-x divide-strip-line text-center">
              <div className="px-4">
                <dt className="font-display text-sm font-bold tracking-widest text-strip-muted uppercase">Season</dt>
                <dd className="big-number text-4xl text-strip-fg">{week.season}</dd>
              </div>
              <div className="px-4">
                <dt className="font-display text-sm font-bold tracking-widest text-strip-muted uppercase">Week</dt>
                <dd className="big-number text-4xl text-strip-hot">{week.week}</dd>
              </div>
            </dl>
            <div className="mt-3 flex flex-wrap items-center justify-center gap-2 text-sm text-strip-muted">
              <KindBadge kind="live" />
              {asOf ? <span>as of {fmtUtc(asOf)}</span> : null}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

export default async function HomePage() {
  const [meta, live, index, gloss] = await Promise.all([getSiteMeta(), getLatestLive(), getListIndex(), getGlossary()]);
  const lists = live ? [...live.lists].sort((a, b) => displayOrder(a.header.position) - displayOrder(b.header.position)) : [];
  const latestRecon = index.find((r) => r.kind === "backtest" && r.season === meta.currentSeason) ?? null;
  const stale =
    live && meta.current && (live.week.season !== meta.current.season || live.week.week !== meta.current.week);
  const flexLists = lists.filter((l) => (FLEX_POSITIONS as readonly string[]).includes(l.header.position));
  const flex = mergeFlex(flexLists.flatMap((l) => l.picks));
  const tops = lists.filter((l) => l.picks.length).map((l) => ({ position: l.header.position, pick: l.picks[0] }));

  return (
    <>
      <Banner week={live && lists.length ? live.week : null} asOf={lists[0]?.header.asOf ?? null} />

      {live && lists.length ? <TopBoard tops={tops} /> : null}

      <TrackHeadline />

      {live && lists.length ? (
        <section aria-labelledby="live-heading" className="mb-10">
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <h2 id="live-heading" className="section-title">
              Waiver Radar, {seasonWeek(live.week.season, live.week.week)}
            </h2>
            <KindBadge kind="live" />
          </div>
          <p className="mb-4 text-sm text-muted">
            Made at the <Term name="as_of">as-of time</Term> {fmtUtc(lists[0].header.asOf)} from the data public then.{" "}
            <Term name="list_kind">Live</Term> lists are never changed afterwards.
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
            {lists.map((l) => {
              const pos = l.header.position;
              return (
                <section
                  key={pos}
                  aria-labelledby={`pos-${pos}`}
                  data-pos={pos}
                  className="pos-edge min-w-0 rounded-xl border border-line bg-surface/50 p-3"
                >
                  <div className="mb-3 flex flex-wrap items-center gap-2">
                    <PosBadge pos={pos} />
                    <h3 id={`pos-${pos}`} className="display text-2xl uppercase">
                      {positionLabel(pos)} ({pos})
                    </h3>
                  </div>
                  {l.header.note ? <p className="mb-2 text-sm font-medium">{l.header.note}</p> : null}
                  {l.picks.length ? (
                    <PickList
                      picks={l.picks.slice(0, TOP)}
                      label={`${pos} top ${TOP}, ${seasonWeek(l.header.season, l.header.week)}, live`}
                      reasons={false}
                      showOutcome={false}
                    />
                  ) : (
                    <p className="text-muted">This list has no players.</p>
                  )}
                  <p className="mt-2 text-sm">
                    <Link href={`/waivers?pos=${pos}&season=${l.header.season}&week=${l.header.week}&kind=live`}>
                      Full {pos} list with reasons and outcomes
                    </Link>
                  </p>
                </section>
              );
            })}
            {flex.length ? (
              <section
                aria-labelledby={`pos-${FLEX}`}
                data-pos={FLEX}
                data-testid="flex-card"
                className="pos-edge min-w-0 rounded-xl border border-line bg-surface/50 p-3 lg:col-span-2"
              >
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <PosBadge pos={FLEX} />
                  <h3 id={`pos-${FLEX}`} className="display text-2xl uppercase">
                    {positionLabel(FLEX)}
                  </h3>
                </div>
                <div className="mb-3">
                  <FlexNote shape={leagueShape(gloss)} order={flexOrderOf(flex)} missing={[]} compact />
                </div>
                <PickList
                  picks={flex.slice(0, TOP)}
                  flex
                  label={`FLEX top ${TOP} (RB, WR and TE merged), ${seasonWeek(live.week.season, live.week.week)}, live`}
                  reasons={false}
                  showOutcome={false}
                />
                <p className="mt-2 text-sm">
                  <Link href={`/waivers?pos=${FLEX}&season=${live.week.season}&week=${live.week.week}&kind=live`}>
                    Full FLEX list with reasons and outcomes
                  </Link>
                </p>
              </section>
            ) : null}
          </div>
        </section>
      ) : (
        <section aria-labelledby="live-heading" className="mb-10">
          <h2 id="live-heading" className="sr-only">
            Waiver Radar
          </h2>
          <EmptyState title="No live list yet">
            <p>
              The first live list appears on a {AS_OF_WEEKDAY} after the as-of time ({AS_OF_TIME_UTC} UTC), once that
              week&apos;s games and data have arrived. A live list is made in real time and never changed afterwards.
            </p>
            {latestRecon ? (
              <p>
                Meanwhile, the newest reconstructed list ({seasonWeek(latestRecon.season, latestRecon.week)}: what the
                Radar would have said that Tuesday) is on the{" "}
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
