import Link from "next/link";
import Term from "@/components/Term";
import { EstimateCell } from "@/components/hot-seat/parts";
import { KindBadge } from "@/components/ui";
import { hotSeatHref, listName } from "@/lib/hot-seat";
import { HOT_SEAT_WINDOW_DAYS } from "@/lib/method";
import { getSiteMeta } from "@/lib/queries/meta";
import { getHotSeatIndex, getHotSeatList } from "@/lib/queries/hot-seat";

const TOP = 5;

/** The home page's Hot-Seat card: the top 5 of this season's newest list (live if that week has
 *  one, else the reconstructed one, labelled), linking to /hot-seat. Shown only while the newest
 *  list belongs to the current season; otherwise nothing is rendered. */
export default async function HotSeatCard() {
  const [index, site] = await Promise.all([getHotSeatIndex(), getSiteMeta()]);
  const top = index[0];
  if (!top || top.season !== site.currentSeason) return null;
  const chosen = index.find((r) => r.kind === "live" && r.season === top.season) ?? top;
  const data = await getHotSeatList(chosen.season, chosen.week, chosen.kind);
  if (!data || !data.rows.length) return null;
  const name = listName(chosen.season, chosen.week, chosen.snapshot);
  const rows = data.rows.slice(0, TOP);
  return (
    <section aria-labelledby="hot-seat-card" className="mt-6 min-w-0 rounded-xl border border-line bg-surface p-4 sm:p-5" data-testid="hot-seat-card">
      <p className="kicker">Hot-Seat Meter</p>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        <h2 id="hot-seat-card" className="section-title">
          Hot seat, {name}
        </h2>
        <KindBadge kind={chosen.kind} />
      </div>
      <p className="mt-2 text-sm text-muted">
        The {TOP} head coaches with the highest <Term name="hot_seat_estimate">estimated chance</Term> of being let go by{" "}
        {HOT_SEAT_WINDOW_DAYS} days after the season: an estimate, not a verdict.
        {chosen.kind === "backtest" ? " This week's list was reconstructed after the fact from the data public at the time." : ""}
      </p>
      <div className="@container mt-4">
        <ol aria-label={`Hot seat top ${TOP}, ${name}, ${chosen.kind === "live" ? "live" : "reconstructed"}`} className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-bg" data-testid="hot-seat-top">
          {rows.map((r) => (
            <li key={r.coachId} data-row="" className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-3 gap-y-1 px-3 py-2.5 @md:grid-cols-[2rem_minmax(0,1fr)_7.5rem]">
              <span data-cell="rank" aria-hidden="true" className="big-number text-xl text-muted">
                {r.rank}
              </span>
              <p data-cell="coach" className="min-w-0 font-semibold break-words">
                <Link href={`/coach/${r.coachId}`}>{r.name}</Link>
                <span className="font-normal text-muted"> · {r.teamName ?? r.team}</span>
                {r.isInterim ? <span className="block text-xs font-normal text-muted">interim: the model was not trained on interim coaches</span> : null}
              </p>
              <div data-cell="estimate" className="col-start-2 @md:col-start-3 @md:text-right">
                <EstimateCell probability={r.probability} size="small" />
              </div>
            </li>
          ))}
        </ol>
      </div>
      <p className="mt-3 text-sm">
        <Link href={hotSeatHref({ season: chosen.season, week: chosen.week, kind: chosen.kind })}>Every coach, the drivers and how to read it</Link>
      </p>
    </section>
  );
}
