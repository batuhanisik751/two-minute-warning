import Link from "next/link";
import { fmtPoints } from "@/lib/format";
import { signed, statsOf, type RegressionRow } from "@/lib/regression";
import { teamStyle } from "@/lib/team-colors";
import { MiniLabel, PosBadge } from "./ui";

// One Regression Watch table (Sell-high, Buy-low or Legit) as rows that lay out by their
// container's width (the Waiver Radar's approach, components/PickList.tsx): below 48rem the
// player, then his numbers as labelled pairs, then the reason; from 48rem one column per number
// with a header row, the reason across the row. Every cell is a grid area with a minimum width
// (tests/smoke/layout.test.ts measures the rows in Chrome from 320 to 1920 px).
const GRID =
  "grid grid-cols-[minmax(0,1fr)] gap-x-3 gap-y-2 [grid-template-areas:'player'_'stats'_'reason'] @3xl:grid-cols-[minmax(0,1fr)_3rem_4.5rem_4.5rem_4.75rem_5.25rem] @3xl:[grid-template-areas:'player_games_ppg_xfp_fpoe_proj'_'reason_reason_reason_reason_reason_reason']";
const HEAD =
  "hidden gap-x-3 @3xl:grid @3xl:grid-cols-[minmax(0,1fr)_3rem_4.5rem_4.5rem_4.75rem_5.25rem] @3xl:[grid-template-areas:'player_games_ppg_xfp_fpoe_proj']";

const CELLS = [
  { key: "games", area: "@3xl:[grid-area:games]", label: "Games", short: "G" },
  { key: "ppg", area: "@3xl:[grid-area:ppg]", label: "PPG", short: "PPG" },
  { key: "xfp", area: "@3xl:[grid-area:xfp]", label: "xFP/game", short: "xFP/g" },
  { key: "fpoe", area: "@3xl:[grid-area:fpoe]", label: "FPOE/game", short: "FPOE/g" },
  { key: "proj", area: "@3xl:[grid-area:proj]", label: "Projection", short: "Proj." },
] as const;

const val = (v: number | null, f: (x: number) => string) => (v === null ? "–" : f(v));

export default function RegressionList({
  rows,
  label,
  withGarbage,
  reasons = true,
  outcomes = true,
}: {
  rows: RegressionRow[];
  label: string;
  withGarbage: boolean;
  reasons?: boolean;
  outcomes?: boolean;
}) {
  return (
    <div className="@container">
      <div aria-hidden="true" data-row="header" className={`${HEAD} px-3 pb-1.5 pl-4 font-display text-[0.8rem] font-bold tracking-wider text-muted uppercase`}>
        <span data-cell="player" className="[grid-area:player]">
          Player
        </span>
        {CELLS.map((c) => (
          <span key={c.key} data-cell={c.key} className={`${c.area} text-right`}>
            {c.short}
          </span>
        ))}
      </div>
      <ol aria-label={label} className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface" data-testid="rw-list">
        {rows.map((r) => (
          <Row key={r.gsisId} r={r} withGarbage={withGarbage} reasons={reasons} outcomes={outcomes} />
        ))}
      </ol>
    </div>
  );
}

function Row({ r, withGarbage, reasons, outcomes }: { r: RegressionRow; withGarbage: boolean; reasons: boolean; outcomes: boolean }) {
  const s = statsOf(r, withGarbage);
  const values: Record<(typeof CELLS)[number]["key"], string> = {
    games: String(r.games),
    ppg: val(s.ppg, fmtPoints),
    xfp: val(s.xfp, fmtPoints),
    fpoe: val(s.fpoe, (x) => signed(x)),
    proj: fmtPoints(r.projection),
  };
  const o = r.outcome;
  // a pending outcome says nothing per row: the page says it once
  const final = outcomes && o !== null && o.status === "final";
  return (
    <li
      data-testid="rw-row"
      data-row="regression"
      data-tags={r.tags.join(" ")}
      style={teamStyle(r.teamColor, r.teamColor2)}
      className={`team-mark team-stripe ${GRID} px-3 py-3 pl-4`}
    >
      <div data-cell="player" className="min-w-0 [grid-area:player]">
        <Link
          href={`/player/${r.gsisId}`}
          className="min-w-0 font-display text-xl leading-tight font-bold tracking-wide text-fg uppercase underline decoration-line-strong decoration-1 underline-offset-4 [overflow-wrap:anywhere] hover:decoration-hot hover:decoration-2"
        >
          {r.name}
        </Link>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm text-muted">
          <PosBadge pos={r.position} />
          <span className="team-swatch" aria-hidden="true" />
          <span className="min-w-0 [overflow-wrap:anywhere]">
            {r.team} · {r.teamName}
          </span>
        </span>
      </div>
      <dl className="flex min-w-0 flex-wrap gap-x-5 gap-y-2 [grid-area:stats] @3xl:contents">
        {CELLS.map((c) => (
          <div key={c.key} data-cell={c.key} className={`min-w-0 tnum ${c.area} @3xl:pt-1 @3xl:text-right`}>
            <dt>
              <MiniLabel className="@3xl:sr-only">{c.label}</MiniLabel>
            </dt>
            <dd className={c.key === "proj" ? "text-lg font-bold" : "text-base"}>{values[c.key]}</dd>
          </div>
        ))}
      </dl>
      {(reasons && r.tagReason) || final ? (
        <div data-cell="reason" className="min-w-0 space-y-1 text-sm [grid-area:reason]">
          {reasons && r.tagReason ? <p className="[overflow-wrap:anywhere]">{r.tagReason}</p> : null}
          {final ? (
            <p className="text-muted" data-testid="rw-outcome">
              {o.rosPpg !== null
                ? `Rest of the season: ${fmtPoints(o.rosPpg)} PPG in ${o.rosGames ?? 0} ${o.rosGames === 1 ? "game" : "games"}.`
                : "Rest of the season: no games played."}
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
