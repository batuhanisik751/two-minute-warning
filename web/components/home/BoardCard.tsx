import Link from "next/link";
import Term from "@/components/Term";
import ThirdPartyNote from "@/components/ThirdPartyNote";
import { EstimateCell } from "@/components/hot-seat/parts";
import { KindBadge, PosBadge } from "@/components/ui";
import { boardHref, boardLinkText, posRank } from "@/lib/board";
import { BOARD_CLIFF_DROP } from "@/lib/method";
import { getBoard, getBoardIndex } from "@/lib/queries/board";
import { getSiteMeta } from "@/lib/queries/meta";

const TOP = 5;

/** The home page's "Cliff watch": the top 5 of the newest board by the chance of a Cliff, with the
 *  chance of missed time beside it (live if that season has one, else the reconstructed one,
 *  labelled), linking to /board. Shown only while the newest board is this season's or the next
 *  one's; otherwise nothing is rendered. */
export default async function BoardCard() {
  const [index, site] = await Promise.all([getBoardIndex(), getSiteMeta()]);
  const chosen = index[0];
  if (!chosen || (site.currentSeason !== null && chosen.season < site.currentSeason)) return null;
  const data = await getBoard(chosen.season, chosen.kind);
  if (!data || !data.rows.length) return null;
  const rows = data.rows.slice(0, TOP);
  const live = chosen.kind === "live";
  return (
    <section aria-labelledby="board-card" className="mt-6 min-w-0 rounded-xl border border-line bg-surface p-4 sm:p-5" data-testid="board-card">
      <p className="kicker">Cliff board</p>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <h2 id="board-card" className="section-title">
          Cliff watch, {chosen.season}
        </h2>
        <KindBadge kind={chosen.kind} />
      </div>
      <p className="mt-2 text-sm text-muted">
        The {TOP} veterans with the highest <Term name="board_cliff_chance">estimated chance of a Cliff</Term> (a drop of{" "}
        {Math.round(BOARD_CLIFF_DROP * 100)}% or more in <Term name="ppg">points per game</Term>), with the separate chance of missed time: estimates, not verdicts.
        {live ? "" : " This board was reconstructed after the fact from the data public on the eve of week 1."}
      </p>
      <div className="@container mt-4">
        <ol aria-label={`Cliff watch top ${TOP}, ${chosen.season}, ${live ? "live" : "reconstructed"}`} className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-bg" data-testid="board-top">
          {rows.map((r) => (
            <li key={r.gsisId} data-row="" className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-3 gap-y-1 px-3 py-2.5 @md:grid-cols-[2rem_minmax(0,1fr)_13rem]">
              <span data-cell="rank" aria-hidden="true" className="big-number text-xl text-muted">
                {r.cliffRank}
              </span>
              <p data-cell="player" className="min-w-0 font-semibold break-words">
                <PosBadge pos={r.position} className="mr-2" />
                <Link href={`/player/${r.gsisId}`}>{r.name}</Link>
                <span className="block text-xs font-normal text-muted">
                  Last season with the {r.teamName ?? r.team}
                  {r.ecrRank !== null ? ` · experts' preseason rank ${posRank(r.position, r.ecrRank)}` : ""}
                </span>
              </p>
              <div className="col-start-2 grid max-w-xs grid-cols-2 gap-3 @md:col-start-3 @md:max-w-none">
                <div data-cell="cliff" className="min-w-0">
                  <EstimateCell probability={r.cliffProbability} size="small" label="Chance of a Cliff" />
                </div>
                <div data-cell="missed" className="min-w-0">
                  <EstimateCell probability={r.missedProbability} size="small" label="Chance of missed time" />
                </div>
              </div>
            </li>
          ))}
        </ol>
      </div>
      <p className="mt-3 text-sm">
        <Link href={boardHref({ season: chosen.season, kind: chosen.kind })}>{boardLinkText()}</Link>
      </p>
      <ThirdPartyNote inline />
    </section>
  );
}
