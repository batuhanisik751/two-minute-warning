import Link from "next/link";
import Term from "@/components/Term";
import { PosBadge } from "@/components/ui";
import { fmtPoints, fmtUtc, pct } from "@/lib/format";
import { chancePct, ifPlaysText, kickoffGroups, opponentText, practiceLabel, type QRow } from "@/lib/questionable";

// This week's Questionable list (feature #1): the players of the newest snapshot, grouped by
// kickoff (earliest first), each group by the chance he plays (highest first). Every cell is a
// stacked block (tests/smoke/layout.test.ts measures [data-row] in Chrome from 320 to 1920 px).

const TAG_TERM: Record<string, string> = { Questionable: "questionable", Doubtful: "doubtful" };

export default function QuestionableList({ rows, label }: { rows: QRow[]; label: string }) {
  return (
    <div className="space-y-6" data-testid="q-list">
      {kickoffGroups(rows).map((g) => (
        <section key={g.kickoff} aria-label={`${label}: kickoff ${fmtUtc(g.kickoff)}`}>
          <h3 className="mb-2 font-display text-lg font-bold tracking-wide uppercase">
            Kickoff <time dateTime={g.kickoff}>{fmtUtc(g.kickoff)}</time>
          </h3>
          <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">
            {g.rows.map((r) => (
              <Row key={r.gsisId} r={r} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Row({ r }: { r: QRow }) {
  const vs = opponentText(r.team, r.opponent, r.gameId);
  const plays = ifPlaysText(r);
  return (
    <li data-testid="q-row" data-row="questionable" data-status={r.reportStatus} className="grid grid-cols-[minmax(0,1fr)] gap-y-1.5 px-3 py-3">
      <div data-cell="player" className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <Link href={`/player/${r.gsisId}`} className="min-w-0 font-display text-xl leading-tight font-bold tracking-wide uppercase [overflow-wrap:anywhere]">
          {r.name}
        </Link>
        <PosBadge pos={r.position} />
        <span className="text-sm text-muted">
          {r.team}
          {vs ? ` ${vs}` : ""}
        </span>
      </div>
      <p data-cell="tag" className="min-w-0 text-sm break-words">
        <strong className="font-semibold" data-testid="q-tag">
          <Term name={TAG_TERM[r.reportStatus] ?? "questionable"}>{r.reportStatus}</Term>
        </strong>
        {" · "}
        <Term name="practice_status">Practice</Term>: {practiceLabel(r.practice)}
        {r.missedPrev ? " · missed his team's last game" : ""}
      </p>
      <p data-cell="chance" className="min-w-0 break-words">
        <Term name="play_chance">Chance he plays</Term>: <strong className="big-number text-2xl" data-testid="q-chance">{chancePct(r.playChance)}</strong>
      </p>
      <p data-cell="plays" className="min-w-0 text-sm break-words text-muted" data-testid="q-plays">
        {plays ? (
          <>
            {plays}.
            {r.playsDudRate !== null ? (
              <>
                {" "}
                <Term name="dud_rate">Dud rate</Term> {pct(r.playsDudRate)}
                {r.healthyDudRate !== null ? ` (healthy: ${pct(r.healthyDudRate)})` : ""}.
              </>
            ) : null}
          </>
        ) : (
          "If he plays: too few past players like him to say how he usually scores."
        )}
      </p>
      <p data-cell="ppg" className="min-w-0 text-sm break-words text-muted">
        {r.seasonPpg !== null ? `His points per game so far: ${fmtPoints(r.seasonPpg)} (${r.seasonGames} ${r.seasonGames === 1 ? "game" : "games"})` : "No game with a snap yet this season"}
      </p>
    </li>
  );
}
