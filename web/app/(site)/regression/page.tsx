import Link from "next/link";
import ScatterFigure, { type ScatterRow } from "@/components/charts/ScatterFigure";
import type { ScatterGroup } from "@/components/charts/XfpScatter";
import RegressionList from "@/components/RegressionList";
import RegressionTrack from "@/components/RegressionTrack";
import Term from "@/components/Term";
import WeekPicker from "@/components/WeekPicker";
import { EmptyState, KindBadge, Note, PageHeader } from "@/components/ui";
import { fmtInt, fmtUtc, kindLabel, seasonWeek } from "@/lib/format";
import { chooseList, parseInt4, parseKind } from "@/lib/params";
import { getSiteMeta } from "@/lib/queries/meta";
import { getRegressionIndex, getRegressionList, getRegressionTrack, type RegressionListData } from "@/lib/queries/regression";
import { TAGS, parseGarbage, regressionHref, signed, statsOf, tagRows, tagTitle, type RegressionRow, type Tag } from "@/lib/regression";
import { pageMetadata } from "@/lib/seo";

export const metadata = pageMetadata("/regression", "Regression Watch", "Regression Watch: the week's sell-high and buy-low players, from expected fantasy points against points per game, with or without garbage time, and its track record.");

const GROUPS: ScatterGroup[] = [
  { key: "sell_high", name: "Sell-high", color: "var(--chart-b)", shape: "triangle" },
  { key: "buy_low", name: "Buy-low", color: "var(--chart-a)", shape: "diamond" },
  { key: "none", name: "No tag", color: "var(--border-strong)", shape: "circle" },
];

const TAG_INTRO: Record<Tag, string> = {
  sell_high: "Scoring well above what his chances were worth, and the projection says it will fade.",
  buy_low: "Scoring well below what his chances were worth, and the projection says it will rise.",
};

function Intro() {
  return (
    <PageHeader title="Regression Watch" kicker="Opportunity against efficiency">
      A player&apos;s points have two parts: <strong>opportunity</strong>, what his targets and carries were worth to an
      average player (<Term name="xfp">xFP</Term>), and <strong>efficiency</strong>, what he made of them (
      <Term name="fpoe">FPOE</Term>, points over expected). Opportunity repeats from week to week; efficiency mostly does
      not, and drifts back toward average (<Term name="regression_to_the_mean">regression to the mean</Term>). Each week
      Regression Watch projects every fantasy-relevant player&apos;s rest of the season from both parts and tags the ones
      whose scoring is out of line with their chances.
    </PageHeader>
  );
}

/** The two views as links (they work without JavaScript and can be shared). */
function GarbageToggle({ chosen, withGarbage }: { chosen: { season: number; week: number; kind: string }; withGarbage: boolean }) {
  const opts = [
    { on: true, label: "With garbage time" },
    { on: false, label: "Without garbage time" },
  ];
  return (
    <nav aria-label="Garbage time" className="flex flex-wrap items-center gap-2" data-testid="gt-toggle">
      {opts.map((o) => (
        <Link
          key={String(o.on)}
          href={regressionHref({ season: chosen.season, week: chosen.week, kind: chosen.kind, withGarbage: o.on })}
          aria-current={o.on === withGarbage ? "page" : undefined}
          className="pos-tab inline-flex min-h-11 items-center justify-center rounded-md px-3 no-underline"
        >
          {o.label}
        </Link>
      ))}
      <span className="text-sm text-muted">
        <Term name="garbage_time_view">What this changes</Term>: PPG, xFP/game and FPOE/game only.
      </span>
    </nav>
  );
}

export default async function RegressionPage({ searchParams }: PageProps<"/regression">) {
  const sp = await searchParams;
  const withGarbage = parseGarbage(sp.gt);
  const [index, meta, track] = await Promise.all([getRegressionIndex(), getSiteMeta(), getRegressionTrack()]);
  const askedSeason = parseInt4(sp.season);
  const askedWeek = parseInt4(sp.week);
  const askedKind = parseKind(sp.kind);
  const { chosen, exact } = chooseList(index, askedSeason, askedWeek, askedKind);
  if (!chosen) {
    return (
      <>
        <Intro />
        <EmptyState title="No Regression Watch list published yet">
          <p>Weekly lists appear here after the first publish that includes Regression Watch.</p>
        </EmptyState>
      </>
    );
  }
  const data = await getRegressionList(chosen.season, chosen.week, chosen.kind);
  const k = kindLabel(chosen.kind, "Regression Watch");
  const other = index.find((r) => r.season === chosen.season && r.week === chosen.week && r.kind !== chosen.kind);
  const weeks = [...new Set(index.filter((r) => r.kind === "backtest").map((r) => r.week))].sort((a, b) => a - b);
  const asked = askedSeason !== null || askedWeek !== null || askedKind !== null;
  return (
    <>
      <Intro />
      <WeekPicker
        index={index}
        chosen={chosen}
        action="/regression"
        hidden={withGarbage ? {} : { gt: "off" }}
        hrefFor={(w) => regressionHref({ season: w.season, week: w.week, withGarbage })}
      />
      {!exact && asked ? (
        <div className="mt-4">
          <Note tone="warn">There is no list for that week; showing the nearest published one instead.</Note>
        </div>
      ) : null}
      <section
        aria-labelledby="rw-heading"
        className={`mt-6 rounded-xl bg-surface/40 p-3 sm:p-4 ${chosen.kind === "live" ? "frame-live" : "frame-recon"}`}
        data-testid="rw-week"
        data-kind={chosen.kind}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-1">
          <h2 id="rw-heading" className="display min-w-0 text-2xl uppercase sm:text-3xl">
            Regression Watch, {seasonWeek(chosen.season, chosen.week)}
          </h2>
          <KindBadge kind={chosen.kind} />
        </div>
        <p className="mt-1 text-sm" data-testid="kind-label">
          <Term name="list_kind">{k.long}</Term>.
        </p>
        {data ? <WeekBody data={data} withGarbage={withGarbage} currentSeason={meta.currentSeason} /> : <EmptyState title="This list could not be read" />}
        {other ? (
          <p className="mt-4 text-sm">
            This week also has a {other.kind === "live" ? "live" : "reconstructed"} list:{" "}
            <Link href={regressionHref({ season: chosen.season, week: chosen.week, kind: other.kind, withGarbage })}>
              show the {other.kind === "live" ? "live" : "reconstructed"} list
            </Link>
            .
          </p>
        ) : null}
      </section>
      <section aria-labelledby="rw-track-heading" className="mt-10">
        <h2 id="rw-track-heading" className="section-title mb-3 scroll-mt-24">
          Track record
        </h2>
        <RegressionTrack rows={track} weeks={weeks} />
        <p className="mt-4 text-sm">
          <Link href="/methodology#regression">How Regression Watch works, by position and in full</Link>
        </p>
      </section>
    </>
  );
}

function WeekBody({ data, withGarbage, currentSeason }: { data: RegressionListData; withGarbage: boolean; currentSeason: number | null }) {
  const { header, rows } = data;
  const chosen = { season: header.season, week: header.week, kind: header.kind };
  const view = withGarbage ? "with garbage time" : "without garbage time";
  const label = (t: Tag) => `${tagTitle(t)}, ${seasonWeek(header.season, header.week)}, ${header.kind === "live" ? "live" : "reconstructed"}`;
  return (
    <>
      <p className="mt-2 text-sm text-muted">
        As of <time dateTime={header.asOf}>{fmtUtc(header.asOf)}</time> (the <Term name="as_of">as-of time</Term>);{" "}
        {header.kind === "live" ? "made" : "reconstructed"} on <time dateTime={header.generatedAt}>{fmtUtc(header.generatedAt)}</time>.{" "}
        {fmtInt(header.nUniverse)} players in the <Term name="regression_universe">universe</Term>; the parameters (
        <span className="font-mono text-xs">{header.paramsVersion}</span>) were chosen on earlier seasons only.
      </p>
      <div className="mt-4 space-y-3">
        {header.incomplete ? <Note tone="warn">This list was made before all of the week&apos;s data had arrived (published as incomplete).</Note> : null}
        {header.note ? <Note tone="warn">{header.note}</Note> : null}
        {header.season < (currentSeason ?? header.season) ? (
          <p className="text-sm text-muted">
            <Term name="current_franchise">Teams</Term> are shown by today&apos;s franchise code and name.
          </p>
        ) : null}
        <GarbageToggle chosen={chosen} withGarbage={withGarbage} />
        {rows.length && rows.every((r) => r.outcome?.status !== "final") ? (
          <p className="text-sm text-muted" data-testid="rw-pending">
            What these players score over the rest of the season appears under each of them once the regular season is over.
          </p>
        ) : null}
        <nav aria-label="On this list" className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {TAGS.map((t) => (
            <a key={t} href={`#tag-${t}`} className="inline-flex min-h-11 items-center">
              {tagTitle(t)} ({rows.filter((r) => r.tags.includes(t)).length})
            </a>
          ))}
          <a href="#scatter-heading" className="inline-flex min-h-11 items-center">
            Points against expected points
          </a>
          <a href="#rw-track-heading" className="inline-flex min-h-11 items-center">
            Track record
          </a>
        </nav>
      </div>
      {TAGS.map((t) => {
        const list = tagRows(rows, t);
        return (
          <section key={t} aria-labelledby={`tag-${t}`} className="mt-8" data-testid={`tag-${t}`}>
            <div className="flex flex-wrap items-baseline gap-x-3">
              <h3 id={`tag-${t}`} className="display scroll-mt-24 text-2xl uppercase">
                <Term name={t}>{tagTitle(t)}</Term> ({list.length})
              </h3>
            </div>
            <p className="mt-1 mb-3 text-sm text-muted">
              {TAG_INTRO[t]} PPG, xFP/game and FPOE/game {view}; projection = the <Term name="ppg_ros">rest-of-season projection</Term> (points per game).
            </p>
            {list.length ? (
              <RegressionList rows={list} label={label(t)} withGarbage={withGarbage} />
            ) : (
              <p className="rounded-lg border border-line bg-surface p-3 text-muted">No {tagTitle(t)} player this week.</p>
            )}
          </section>
        );
      })}
      <section aria-labelledby="scatter-heading" className="mt-10">
        <h3 id="scatter-heading" className="display scroll-mt-24 text-2xl uppercase">
          Points against expected points
        </h3>
        <p className="mt-1 mb-3 text-sm text-muted">
          Every universe player, {view}: above the dashed diagonal he scored more than his chances were worth (positive FPOE),
          below it less. The further from the line, the more of his scoring is efficiency, the part that tends to fade.
        </p>
        <ScatterFigure
          id="rw-scatter"
          caption={`PPG against xFP per game, ${seasonWeek(header.season, header.week)} (${view})`}
          rows={scatterRows(rows, withGarbage)}
          groups={GROUPS}
          xLabel="xFP/game"
          yLabel="PPG"
          extraLabel="FPOE/game"
        />
      </section>
    </>
  );
}

function scatterRows(rows: RegressionRow[], withGarbage: boolean): ScatterRow[] {
  const out: ScatterRow[] = [];
  for (const r of rows) {
    const s = statsOf(r, withGarbage);
    if (s.ppg === null || s.xfp === null) continue;
    const group = r.tag ?? "none";
    out.push({
      id: r.gsisId,
      x: s.xfp,
      y: s.ppg,
      name: r.name,
      group,
      position: r.position,
      team: r.team,
      extra: s.fpoe === null ? "–" : signed(s.fpoe),
      groupName: r.tags.length ? r.tags.map(tagTitle).join(", ") : "No tag",
    });
  }
  return out;
}
