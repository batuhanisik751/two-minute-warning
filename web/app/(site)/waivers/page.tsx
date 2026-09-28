import Link from "next/link";
import BucketBadges from "@/components/BucketBadges";
import PickList from "@/components/PickList";
import PositionTabs from "@/components/PositionTabs";
import Term from "@/components/Term";
import WeekPicker from "@/components/WeekPicker";
import { EmptyState, KindBadge, Note, PageHeader } from "@/components/ui";
import { fmtInt, fmtUtc, kindLabel, pct, seasonWeek } from "@/lib/format";
import { CHANCE_RANGE_LEVEL, POSITION_NAMES } from "@/lib/method";
import { chooseList, parseInt4, parseKind, parsePosition, waiversHref } from "@/lib/params";
import { getSiteMeta } from "@/lib/queries/meta";
import { getBucketCounts, getList, getListIndex } from "@/lib/queries/radar";

export const metadata = { title: "Waivers" };

export default async function WaiversPage({ searchParams }: PageProps<"/waivers">) {
  const sp = await searchParams;
  const position = parsePosition(sp.pos);
  const askedSeason = parseInt4(sp.season);
  const askedWeek = parseInt4(sp.week);
  const askedKind = parseKind(sp.kind);
  const [index, meta] = await Promise.all([getListIndex(), getSiteMeta()]);
  const { chosen, exact } = chooseList(index, askedSeason, askedWeek, askedKind);

  const intro = (
    <PageHeader title="Waiver Radar">
      Each position&apos;s weekly list of players who are probably still on waivers (the{" "}
      <Term name="candidate_pool">candidate pool</Term>), ranked by their <Term name="chance">chance</Term> of becoming a
      fantasy starter soon. Pick a season and week to see what the Radar said then and what happened next.
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

  const [data, counts] = await Promise.all([
    getList(chosen.season, chosen.week, position, chosen.kind),
    getBucketCounts(position, chosen.season),
  ]);
  const other = index.find(
    (r) => r.season === chosen.season && r.week === chosen.week && r.kind !== chosen.kind,
  );
  const k = kindLabel(chosen.kind);
  const title = `${POSITION_NAMES[position]} (${position}), ${seasonWeek(chosen.season, chosen.week)}`;

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
      <PositionTabs current={position} chosen={chosen} />
      <WeekPicker index={index} chosen={chosen} position={position} />

      {!exact && asked ? (
        <div className="mt-4">
          <Note tone="warn">
            There is no list for {asked}; showing the nearest published one instead.
          </Note>
        </div>
      ) : null}

      <section
        aria-labelledby="list-heading"
        className={`mt-6 rounded-xl p-4 ${chosen.kind === "live" ? "frame-live" : "frame-recon"}`}
        data-testid="radar-list"
        data-kind={chosen.kind}
      >
        <div className="flex flex-wrap items-center gap-3">
          <h2 id="list-heading" className="text-xl font-semibold">
            {title}
          </h2>
          <KindBadge kind={chosen.kind} />
        </div>
        <p className="mt-1 text-sm" data-testid="kind-label">
          <Term name="list_kind">{k.long}</Term>.
        </p>

        {!data ? (
          <div className="mt-4">
            <EmptyState title={`No ${position} list for this week`}>
              <p>
                {seasonWeek(chosen.season, chosen.week)} has lists for other positions only. Choose another position
                above.
              </p>
            </EmptyState>
          </div>
        ) : (
          <>
            <p className="mt-2 text-sm text-muted">
              As of <time dateTime={data.header.asOf}>{fmtUtc(data.header.asOf)}</time> (the{" "}
              <Term name="as_of">as-of time</Term>);{" "}
              {chosen.kind === "live" ? "made" : "reconstructed"} on{" "}
              <time dateTime={data.header.generatedAt}>{fmtUtc(data.header.generatedAt)}</time>. Ranked from{" "}
              {fmtInt(data.header.nPool)} players in the pool; the top {data.picks.length} are shown.
              {data.header.trainingSeasons.length ? (
                <>
                  {" "}
                  The model learned from {Math.min(...data.header.trainingSeasons)}
                  {data.header.trainingSeasons.length > 1 ? `–${Math.max(...data.header.trainingSeasons)}` : ""}{" "}
                  only.
                </>
              ) : null}
            </p>

            <div className="mt-4 space-y-3">
              {data.header.incomplete ? (
                <Note tone="warn">
                  This list was made before all of the week&apos;s data had arrived (published as incomplete).
                </Note>
              ) : null}
              {data.header.note ? <Note tone="warn">{data.header.note}</Note> : null}
              {data.picks.length && data.picks.every((p) => p.chance === null) ? (
                <Note>
                  <strong>Chance and priority: not available for this list.</strong> The chance is how often similar
                  players hit in earlier seasons&apos; backtests; it was not computed for this reconstructed list,
                  which shows the rank and what happened afterwards. The model&apos;s raw probability is not shown in
                  its place.
                </Note>
              ) : null}
              {data.picks.length && data.picks.every((p) => p.reasons.length === 0) ? (
                <Note>
                  <strong>No reasons for this list.</strong> Reasons are written by the weekly list; the walk-forward
                  backtest that reconstructed this list did not generate them.
                </Note>
              ) : null}
              {chosen.season < (meta.currentSeason ?? chosen.season) ? (
                <p className="text-sm text-muted">
                  <Term name="current_franchise">Teams</Term> are shown by today&apos;s franchise code and name.
                </p>
              ) : null}
            </div>

            <div className="mt-4">
              <BucketBadges counts={counts} position={position} beforeSeason={chosen.season} />
            </div>

            <p className="mt-4 text-sm text-muted">
              <Term name="chance">Chance</Term>: with the {pct(CHANCE_RANGE_LEVEL)} range of how often similar
              players hit. <Term name="priority">Priority</Term>: the suggested pickup priority.{" "}
              <Term name="y_hit" showFormula>
                Outcome
              </Term>
              : hit, no hit, or pending while the weeks after the list are still being played (W = week, then his
              weekly finish at the position).
            </p>

            <div className="mt-3">
              {data.picks.length ? (
                <PickList
                  picks={data.picks}
                  position={position}
                  label={`${position} list, ${seasonWeek(chosen.season, chosen.week)}, ${k.short.toLowerCase()}`}
                />
              ) : (
                <EmptyState title="This list has no players" />
              )}
            </div>
          </>
        )}

        {other ? (
          <p className="mt-4 text-sm">
            This week also has a {other.kind === "live" ? "live" : "reconstructed"} list:{" "}
            <Link href={waiversHref({ pos: position, season: chosen.season, week: chosen.week, kind: other.kind })}>
              show the {other.kind === "live" ? "live" : "reconstructed"} {position} list
            </Link>
            .
          </p>
        ) : null}
      </section>
    </>
  );
}
