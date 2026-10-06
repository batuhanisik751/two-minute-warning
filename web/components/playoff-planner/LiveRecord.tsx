import { StatTiles } from "@/components/track/parts";
import { fmtInt } from "@/lib/format";
import { liveOf, type PPLive } from "@/lib/playoff-planner";
import { positionShort } from "@/lib/positions";

const err = (x: number | null) => (x === null ? "–" : x.toFixed(3));

/** The season's live record (playoff_planner_live), a [data-live] block like /track-record's
 *  others ("graded", "pending" or "none"; no table): each team-game of the playoff weeks graded
 *  with the last rating made before that week, its error against a flat 1.00. Empty until the
 *  last playoff week is played. */
export default function LiveRecord({ rows, season, lastWeek }: { rows: PPLive[]; season: number | null; lastWeek: number }) {
  const mine = liveOf(rows, season);
  const all = mine.find((r) => r.position === "all");
  if (!all || all.n === 0 || all.maeRating === null) {
    return (
      <div data-testid="pp-live-empty" data-live={all?.pending ? "pending" : "none"}>
        <p className="font-semibold">No live results yet.</p>
        <p className="mt-1 text-sm text-muted">
          The playoff weeks are graded after week {lastWeek}: each team&apos;s last rating made before a game against the points its players really scored, next to the
          plain guess that every matchup is average (1.00).
        </p>
      </div>
    );
  }
  const tiles = [
    { label: "Rating's error", term: { name: "mae", text: "MAE" }, value: err(all.maeRating), note: `Every matchup 1.00: ${err(all.maeFlat)}` },
    { label: "Team-games graded", value: fmtInt(all.n), note: `${fmtInt(all.weeks)} playoff ${all.weeks === 1 ? "week" : "weeks"}` },
    { label: "Still waiting", value: fmtInt(all.pending), note: "games not in yet" },
  ];
  return (
    <div className="space-y-3" data-testid="pp-live" data-live="graded">
      <p>
        Over <strong className="tnum">{fmtInt(all.n)}</strong> graded team-games, the ratings missed the real matchup by {err(all.maeRating)} on average, against{" "}
        {err(all.maeFlat)} for calling every matchup average.
      </p>
      <StatTiles testId="pp-live-tiles" stats={tiles} />
      <ul className="space-y-1 text-sm" data-testid="pp-live-positions">
        {mine
          .filter((r) => r.position !== "all")
          .map((r) => (
            <li key={r.position} data-pos={r.position}>
              <span className="font-semibold">{positionShort(r.position)}</span>: {err(r.maeRating)} vs {err(r.maeFlat)} for 1.00 ({fmtInt(r.n)} team-games)
            </li>
          ))}
      </ul>
    </div>
  );
}
