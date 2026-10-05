import Term from "@/components/Term";
import { StatTiles } from "@/components/track/parts";
import { fmtInt, pct } from "@/lib/format";
import { liveRecord, type TOLiveRow } from "@/lib/teammate-out";

/** The season's live record (teammate_out_live): graded teammates, the points MAE against
 *  "nothing changes", the 80% range's coverage and the top gainer named (a [data-live] block:
 *  "graded", "pending" or "none", as /track-record's other live blocks). */
export default function LiveRecord({ rows, season }: { rows: TOLiveRow[]; season: number | null }) {
  const r = liveRecord(rows, season);
  if (!r || r.n === 0 || r.maePoints === null) {
    return (
      <div data-testid="to-live-empty" data-live={r?.pending ? "pending" : "none"}>
        <p className="font-semibold">No live results yet.</p>
        <p className="mt-1 text-sm text-muted">
          {r?.pending ? `${fmtInt(r.pending)} listed ${r.pending === 1 ? "teammate is" : "teammates are"} waiting for their game. ` : ""}A teammate is graded once his
          game is in: his real shares and PPR points against the prediction made before kickoff.
        </p>
      </div>
    );
  }
  const f = (x: number | null) => (x === null ? "–" : x.toFixed(2));
  const tiles = [
    { label: "Points MAE", term: { name: "mae", text: "MAE" }, value: f(r.maePoints), note: `"Nothing changes": ${f(r.maePointsBase)}` },
    { label: "Inside the 80% range", value: r.coverage === null ? "–" : pct(r.coverage), note: `${fmtInt(r.ranged)} teammates with a range` },
    { label: "Top gainer named", value: r.topHit === null ? "–" : pct(r.topHit), note: `${fmtInt(r.teamWeeks)} team-games` },
  ];
  const skipped = r.starterPlayed + r.didNotPlay;
  const inside = r.coverage === null ? 0 : Math.round(r.coverage * r.ranged);
  return (
    <div className="space-y-3" data-testid="to-live" data-live="graded">
      <p>
        <strong className="tnum">{fmtInt(inside)}</strong> of the <strong className="tnum">{fmtInt(r.ranged)}</strong> graded teammates landed inside their 80% range (
        <strong className="tnum">{r.coverage === null ? "–" : pct(r.coverage, 1)}</strong>), from {fmtInt(r.weeks)} live {r.weeks === 1 ? "week" : "weeks"}: the
        predicted <Term name="ppr">PPR points</Term> missed by {f(r.maePoints)} on average, against {f(r.maePointsBase)} for &quot;nothing changes&quot; (his usual
        points).
      </p>
      <StatTiles testId="to-live-tiles" stats={tiles} />
      <p className="text-sm text-muted">
        Each teammate&apos;s last list before his kickoff counts once.
        {skipped ? ` Not graded: ${fmtInt(r.starterPlayed)} because the starter played after all, ${fmtInt(r.didNotPlay)} because the teammate did not play.` : ""}
        {r.pending ? ` ${fmtInt(r.pending)} more ${r.pending === 1 ? "is" : "are"} waiting for their game.` : ""}
      </p>
    </div>
  );
}
