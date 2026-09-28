import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import ChartFigure, { type Column } from "@/components/charts/ChartFigure";
import type { Series } from "@/components/charts/WeeklyChart";
import Term from "@/components/Term";
import { EmptyState, KindBadge, OutcomeBadge, PageHeader, TierBadge } from "@/components/ui";
import { fmtPoints, fmtShare, outcomeOf, pct, pctRange, windowSummary } from "@/lib/format";
import { isGsisId, parseInt4, waiversHref } from "@/lib/params";
import { getPlayer, getPlayerSeasons, getPlayerWeeks, type WeekRow } from "@/lib/queries/player";
import { getPlayerHistory } from "@/lib/queries/radar";

// No loading.tsx above this route on purpose: an unknown id must answer with a real 404
// status, which needs notFound() before the response starts streaming.

export async function generateMetadata({ params }: PageProps<"/player/[id]">): Promise<Metadata> {
  const { id } = await params;
  if (!isGsisId(id)) return { title: "Player not found" };
  const p = await getPlayer(id);
  return { title: p ? p.name : "Player not found" };
}

const points = (v: number | null) => (v === null ? "no data" : fmtPoints(v));
const share = (v: number | null) => (v === null ? "no data" : fmtShare(v));

function shareSeries(position: string): { series: Series[]; columns: Column[]; caption: string } {
  const target: Series = { key: "targetShare", name: "Target share", kind: "line", color: "var(--chart-a)" };
  const carry: Series = { key: "carryShare", name: "Carry share", kind: "line", color: "var(--chart-b)" };
  const tCol: Column = { key: "targetShare", label: "Target share", format: share };
  const cCol: Column = { key: "carryShare", label: "Carry share", format: share };
  if (position === "RB") return { series: [target, carry], columns: [tCol, cCol], caption: "Target share and carry share by week" };
  if (position === "QB") return { series: [carry], columns: [cCol], caption: "Carry share by week" };
  return { series: [target], columns: [tCol], caption: "Target share by week" };
}

export default async function PlayerPage({ params, searchParams }: PageProps<"/player/[id]">) {
  const { id } = await params;
  if (!isGsisId(id)) notFound();
  const player = await getPlayer(id);
  if (!player) notFound();

  const sp = await searchParams;
  const [weekSeasons, history] = await Promise.all([getPlayerSeasons(id), getPlayerHistory(id)]);
  const seasons = [...new Set([...weekSeasons, ...history.map((h) => h.season)])].sort((a, b) => b - a);
  const asked = parseInt4(sp.season);
  const season = asked !== null && seasons.includes(asked) ? asked : (seasons[0] ?? null);
  const weeks: WeekRow[] = season !== null ? await getPlayerWeeks(id, season) : [];
  const seasonHistory = history.filter((h) => h.season === season);
  const otherSeasons = [...new Set(history.filter((h) => h.season !== season).map((h) => h.season))];
  const position = weeks[0]?.position ?? player.position ?? "";

  const data = weeks.map((w) => ({
    week: w.week,
    fantasyPoints: w.fantasyPoints,
    xfp: w.xfp,
    fpoe: w.fpoe,
    snapShare: w.snapShare,
    targetShare: w.targetShare,
    carryShare: w.carryShare,
  }));
  const shares = shareSeries(position);
  const teams = [...new Set(weeks.map((w) => w.team))];

  const draft =
    player.draftYear !== null
      ? `Drafted ${player.draftYear}${player.draftRound !== null ? `, round ${player.draftRound}` : ""}${
          player.draftPick !== null ? `, pick ${player.draftPick}` : ""
        }`
      : "No draft record";

  return (
    <>
      <PageHeader title={player.name}>
        <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-fg" data-testid="player-header">
          <div>
            <dt className="inline text-muted">
              <Term name="listed_position">Listed position</Term>:{" "}
            </dt>
            <dd className="inline font-medium">{player.position ?? "none"}</dd>
          </div>
          <div>
            <dt className="inline text-muted">Team: </dt>
            <dd className="inline font-medium">
              {player.team ? `${player.team}${player.teamName ? ` · ${player.teamName}` : ""}` : "none listed"}
            </dd>
          </div>
          <div>
            <dt className="inline text-muted">Draft: </dt>
            <dd className="inline font-medium">{draft}</dd>
          </div>
          {player.rookieSeason !== null ? (
            <div>
              <dt className="inline text-muted">Rookie season: </dt>
              <dd className="inline font-medium">{player.rookieSeason}</dd>
            </div>
          ) : null}
        </dl>
      </PageHeader>

      {seasons.length === 0 || season === null ? (
        <EmptyState title="No published weeks for this player">
          <p>There are no regular-season weeks or Radar lists for him in the published data.</p>
        </EmptyState>
      ) : (
        <>
          <nav aria-label="Season" className="mb-6">
            <p className="mb-2 text-sm font-medium">Season</p>
            <ul className="flex flex-wrap gap-2">
              {seasons.map((s) => (
                <li key={s}>
                  <Link
                    href={`/player/${id}?season=${s}`}
                    aria-current={s === season ? "page" : undefined}
                    className={`inline-flex min-h-11 items-center rounded-md border px-3 text-sm font-semibold no-underline ${
                      s === season ? "border-accent bg-accent text-on-accent" : "border-line bg-surface text-fg hover:border-accent"
                    }`}
                  >
                    {s}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>

          <section aria-labelledby="weekly-heading" className="mb-10">
            <h2 id="weekly-heading" className="mb-1 text-xl font-semibold">
              {season} week by week
            </h2>
            {weeks.length ? (
              <>
                <p className="mb-4 text-sm text-muted">
                  Regular season, {weeks.length} {weeks.length === 1 ? "week" : "weeks"} with snaps or a stat line
                  {teams.length ? ` (${teams.join(", ")}; ` : " ("}
                  <Term name="current_franchise">today&apos;s franchise codes</Term>).{" "}
                  <Term name="fantasy_points">Fantasy points</Term> are scored from his stats with the site&apos;s scoring settings;{" "}
                  <Term name="xfp">xFP</Term> is what an average player would have scored from the same chances and{" "}
                  <Term name="fpoe">FPOE</Term> the difference;{" "}
                  <Term name="offense_snap_share">snap share</Term>, <Term name="target_share">target share</Term> and{" "}
                  <Term name="carry_share">carry share</Term> are shares of his team&apos;s plays.
                </p>
                <div className="grid gap-6">
                  <ChartFigure
                    id="points"
                    caption="Fantasy points and expected points (xFP) by week"
                    data={data}
                    series={[
                      { key: "fantasyPoints", name: "Fantasy points", kind: "bar", color: "var(--chart-a)" },
                      { key: "xfp", name: "xFP", kind: "line", color: "var(--chart-b)" },
                    ]}
                    columns={[
                      { key: "fantasyPoints", label: "Fantasy points", format: points },
                      { key: "xfp", label: "xFP", format: points },
                      { key: "fpoe", label: "FPOE", format: points },
                    ]}
                  />
                  <ChartFigure
                    id="snaps"
                    caption="Snap share by week"
                    percent
                    data={data}
                    series={[{ key: "snapShare", name: "Snap share", kind: "line", color: "var(--chart-c)" }]}
                    columns={[{ key: "snapShare", label: "Snap share", format: share }]}
                  />
                  <ChartFigure
                    id="shares"
                    caption={shares.caption}
                    percent
                    data={data}
                    series={shares.series}
                    columns={shares.columns}
                  />
                </div>
              </>
            ) : (
              <EmptyState title={`No regular-season weeks in ${season}`}>
                <p>He has no snaps or stat line in the published {season} regular-season data.</p>
              </EmptyState>
            )}
          </section>

          <section aria-labelledby="history-heading">
            <h2 id="history-heading" className="mb-1 text-xl font-semibold">
              Waiver Radar history, {season}
            </h2>
            {seasonHistory.length ? (
              <div className="table-scroll mt-3">
                <table className="data-table" data-testid="radar-history">
                  <caption className="sr-only">Waiver Radar lists with {player.name}, {season}</caption>
                  <thead>
                    <tr>
                      <th scope="col">Week</th>
                      <th scope="col">List</th>
                      <th scope="col" className="num">
                        Rank
                      </th>
                      <th scope="col">
                        <Term name="chance">Chance</Term>
                      </th>
                      <th scope="col">Priority</th>
                      <th scope="col">
                        <Term name="y_hit" showFormula>
                          Outcome
                        </Term>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {seasonHistory.map((h) => {
                      const o = outcomeOf(h.outcome?.yHit ?? null, h.outcome?.status ?? null);
                      const detail =
                        h.outcome?.status === "final"
                          ? windowSummary(h.outcome.windowWeeks, h.outcome.windowRanks, h.outcome.windowPoints, h.position)
                          : null;
                      return (
                        <tr key={`${h.week}-${h.position}-${h.kind}`}>
                          <th scope="row">
                            <Link href={waiversHref({ pos: h.position, season: h.season, week: h.week, kind: h.kind })}>
                              Week {h.week}
                            </Link>
                          </th>
                          <td>
                            {h.position} <KindBadge kind={h.kind} />
                          </td>
                          <td className="num">{h.rank}</td>
                          <td className="tnum">
                            {h.chance !== null ? (
                              <>
                                {pct(h.chance)}
                                {h.chanceLow !== null && h.chanceHigh !== null ? (
                                  <span className="block text-xs text-muted">
                                    similar players hit {pctRange(h.chanceLow, h.chanceHigh)}
                                  </span>
                                ) : null}
                              </>
                            ) : (
                              <span className="text-muted">Not available for this list</span>
                            )}
                          </td>
                          <td>
                            <TierBadge tier={h.tier} />
                          </td>
                          <td>
                            <OutcomeBadge outcome={o} />
                            {detail ? <span className="mt-1 block text-xs text-muted">{detail}</span> : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="mt-2 text-muted">He is not on any published Radar list of {season}.</p>
            )}
            {otherSeasons.length ? (
              <p className="mt-3 text-sm">
                Also on Radar lists in:{" "}
                {otherSeasons.map((s, i) => (
                  <span key={s}>
                    {i > 0 ? ", " : ""}
                    <Link href={`/player/${id}?season=${s}`}>{s}</Link> (
                    {history.filter((h) => h.season === s).length})
                  </span>
                ))}
                .
              </p>
            ) : null}
          </section>
        </>
      )}
    </>
  );
}
