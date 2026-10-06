import Link from "next/link";
import { band, BAND_LABEL, fmtRating, opponentLabel, plannerHref } from "@/lib/playoff-planner";
import { getPlannerMeta, getPlayerPlayoffWeeks } from "@/lib/queries/playoff-planner";

/** On a player page: "Playoff weeks: 15 vs KC 1.08 easy · 16 @ BUF 0.97 neutral · 17 Bye" from
 *  the newest playoff-planner snapshot, when his position is rated (QB, RB, WR, D/ST in the 2026
 *  pin); nothing otherwise. Links to the grid of his position. */
export default async function PlayerLine({ team, position }: { team: string | null; position: string | null }) {
  if (!team || !position) return null;
  const [got, meta] = await Promise.all([getPlayerPlayoffWeeks(team, position), getPlannerMeta()]);
  if (!got) return null;
  const parts = meta.weeks.map((w) => {
    const r = got.rows.find((x) => x.week === w);
    const opp = opponentLabel({ opponent: r?.opponent ?? null, home: r?.home ?? null });
    return r?.opponent && r.rating !== null ? `${w}: ${opp} ${fmtRating(r.rating)} ${BAND_LABEL[band(r.rating)].toLowerCase()}` : `${w}: ${opp}`;
  });
  return (
    <p className="mb-6" data-testid="pp-player-line">
      <Link
        href={plannerHref({ pos: position })}
        className="inline-flex min-h-11 flex-wrap items-center gap-x-2 rounded-md border border-line-strong bg-surface px-3 py-1.5 font-semibold no-underline"
      >
        <span>Playoff weeks ({team}) · {parts.join(" · ")}</span>
        <span className="text-sm font-normal text-muted">(matchups through week {got.header.throughWeek})</span>
      </Link>
    </p>
  );
}
