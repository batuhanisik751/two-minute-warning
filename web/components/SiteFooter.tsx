// The footer of every page: the disclaimer of PROJECT_SPEC 16 (verbatim) and the data
// credits. Each credit was checked against the code that uses the data:
// - nflverse: every dataset (src/twm/sources/nflverse.py), loaded with nflreadpy.
// - ffverse's ffopportunity: per-play and weekly expected-points data (fact_opportunity_*),
//   downloaded from github.com/ffverse/ffopportunity by nflreadpy's load_ff_opportunity: the
//   Waiver Radar's xFP features; the plays the own walk-forward xFP values (Regression Watch
//   and, since 2026-10-02, the player pages, which showed ffopportunity's xFP before).
// - DynastyProcess: the player id map (load_ff_playerids) and the FantasyPros rankings
//   archive (load_ff_rankings), both from github.com/dynastyprocess/data.
// - FantasyPros: the expert rankings in that archive (the candidate pool's preseason list
//   from 2020, the experts' baseline in the track record, the "ranked before the season"
//   reasons).
// - Pro Football Reference: the source of nflverse's snap counts (snap share).
// - Wikipedia: the public pages the Hot-Seat Meter's head-coach departure labels were researched
//   from (one cited URL per row, docs/labeling_coaches.md; mostly each season's "NFL season"
//   page). The owner accepted that research in bulk (docs/progress.md, 2026-10-01): never
//   describe the labels as individually checked.
// No FTN credit: ftn_charting and participation are downloaded but no published number
// uses them.
import Link from "next/link";
import { DISCLAIMER, REPO_URL } from "@/lib/site";

export default function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-line bg-raised">
      <div className="yard-rule" aria-hidden="true" />
      <div className="mx-auto max-w-6xl space-y-3 px-4 py-6 text-sm text-muted">
        <p className="display text-lg uppercase tracking-wider text-fg">Two-Minute Warning</p>
        <p data-testid="disclaimer" className="font-medium text-fg">
          {DISCLAIMER}
        </p>
        <p data-testid="attribution">
          Data from <a href="https://nflverse.nflverse.com/">nflverse</a> (play-by-play, player
          stats, snap counts, rosters, schedules). Expected-points play data from ffverse&apos;s{" "}
          <a href="https://github.com/ffverse/ffopportunity">ffopportunity</a>. Player id map
          and the <a href="https://www.fantasypros.com/">FantasyPros</a> expert rankings archive
          from <a href="https://github.com/dynastyprocess/data">DynastyProcess</a>. Snap counts
          originate from <a href="https://www.pro-football-reference.com/">Pro Football Reference</a>. Head-coach
          departures researched from cited public pages, mostly <a href="https://en.wikipedia.org/">Wikipedia</a>,
          and accepted by the project&apos;s owner in bulk, not re-checked row by row.
        </p>
        <p>
          No NFL or team logos are used. Code under the MIT license on{" "}
          <a href={REPO_URL}>GitHub</a>. How it
          works: <Link href="/methodology">Methodology</Link>.
        </p>
      </div>
    </footer>
  );
}
