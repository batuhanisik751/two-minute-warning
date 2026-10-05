import Link from "next/link";
import Term from "@/components/Term";
import { PosBadge } from "@/components/ui";
import { fmtPoints, fmtShare, fmtUtc } from "@/lib/format";
import { opponentText } from "@/lib/questionable";
import { efficiencyText, gainText, gameGroups, pointsWithRange, reasonLabel, shareMove, type TeamGroup, type TORow } from "@/lib/teammate-out";

// This week's Teammate-out list (feature #5): the newest snapshot's teams grouped by kickoff
// (earliest first); per team its absent starters (why each is out) and the listed teammates by
// predicted points. Every cell is a stacked block (tests/smoke/layout.test.ts measures [data-row]
// in Chrome from 320 to 1920 px).

export default function TeammateList({ rows, label }: { rows: TORow[]; label: string }) {
  return (
    <div className="space-y-8" data-testid="to-list">
      {gameGroups(rows).map((g) => (
        <section key={g.kickoff} aria-label={`${label}: kickoff ${fmtUtc(g.kickoff)}`} className="space-y-4">
          <h3 className="font-display text-lg font-bold tracking-wide uppercase">
            Kickoff <time dateTime={g.kickoff}>{fmtUtc(g.kickoff)}</time>
          </h3>
          {g.teams.map((t) => (
            <Team key={t.team} t={t} />
          ))}
        </section>
      ))}
    </div>
  );
}

function Team({ t }: { t: TeamGroup }) {
  const vs = opponentText(t.team, t.opponent, t.gameId);
  const r0 = t.rows[0];
  const vac = [
    r0?.vacCarryShare ? `${fmtShare(r0.vacCarryShare)} of the carries` : null,
    r0?.vacTargetShare ? `${fmtShare(r0.vacTargetShare)} of the targets` : null,
  ].filter(Boolean);
  return (
    <article className="rounded-lg border border-line bg-surface" data-testid="to-team" data-team={t.team}>
      <header className="space-y-1 border-b border-line px-3 py-3">
        <h4 className="font-display text-xl font-bold tracking-wide uppercase">
          {t.team}
          {vs ? <span className="text-base font-normal text-muted normal-case"> {vs}</span> : null}
        </h4>
        <ul className="space-y-0.5 text-sm" data-testid="to-absent">
          {t.absent.map((a) => (
            <li key={a.gsisId} data-out={a.gsisId}>
              <Term name="starter_out">Starter out</Term>:{" "}
              <Link href={`/player/${a.gsisId}`} className="font-semibold">
                {a.name}
              </Link>
              {a.position ? ` (${a.position})` : ""}, {reasonLabel(a.reason)}
            </li>
          ))}
        </ul>
        {vac.length ? (
          <p className="text-sm text-muted">
            <Term name="vacated_share">Up for grabs</Term>: {vac.join(" and ")} in his usual games.
          </p>
        ) : null}
      </header>
      <ul className="divide-y divide-line">
        {t.rows.map((r) => (
          <Mate key={r.gsisId} r={r} />
        ))}
      </ul>
    </article>
  );
}

function Mate({ r }: { r: TORow }) {
  return (
    <li data-testid="to-row" data-row="teammate-out" data-role={r.role} className="grid grid-cols-[minmax(0,1fr)] gap-y-1.5 px-3 py-3">
      <div data-cell="player" className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <Link href={`/player/${r.gsisId}`} className="min-w-0 font-display text-lg leading-tight font-bold tracking-wide uppercase [overflow-wrap:anywhere]">
          {r.name}
        </Link>
        <PosBadge pos={r.position} />
        <span className="text-sm text-muted">
          <Term name="teammate_role">Role</Term> {r.role}
        </span>
      </div>
      <p data-cell="shares" className="min-w-0 text-sm break-words">
        <Term name="carry_share">Carry share</Term> <span className="tnum">{shareMove(r.baseCarryShare, r.predCarryShare)}</span>
        {" · "}
        <Term name="target_share">Target share</Term> <span className="tnum">{shareMove(r.baseTargetShare, r.predTargetShare)}</span>
      </p>
      <p data-cell="points" className="min-w-0 break-words">
        <Term name="ppr">PPR points</Term>: <strong className="big-number text-xl tnum" data-testid="to-points">{pointsWithRange(r)}</strong>
        <span className="text-sm text-muted">
          {" "}
          80% range · usually {r.basePoints === null ? "–" : fmtPoints(r.basePoints)} a game; the share change is worth {gainText(r.predGain)}
          {efficiencyText(r)}
        </span>
      </p>
    </li>
  );
}
