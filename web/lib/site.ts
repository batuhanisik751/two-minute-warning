// Text that every page carries (PROJECT_SPEC 16), in a plain module so the tests can import
// it without React.
export const DISCLAIMER =
  "Two-Minute Warning is an unofficial, educational project. It is not affiliated with or endorsed by the NFL or ESPN. Predictions are probabilistic and frequently wrong. Not betting advice.";

/** The sources credited in the footer (components/SiteFooter.tsx says why each one). */
export const CREDITS = ["nflverse", "ffopportunity", "DynastyProcess", "FantasyPros", "Pro Football Reference", "Wikipedia"] as const;

/** The public repository (docs/progress.md, 2026-09-27): the one place the site names it. */
export const REPO_URL = "https://github.com/batuhanisik751/two-minute-warning";

/** A file of the repository on GitHub, e.g. docUrl("docs/timemachine.md"). */
export function docUrl(path: string): string {
  return `${REPO_URL}/blob/main/${path}`;
}

/**
 * The model cards (docs/model_cards/, step I4a), one per production model, in the order of
 * their index (docs/model_cards/README.md). tests/unit/site.test.ts checks this list against
 * the folder, so a new card cannot be left off /methodology.
 */
export const MODEL_CARDS = [
  { file: "waiver_radar.md", title: "Waiver Radar" },
  { file: "streamer_k.md", title: "K streamer" },
  { file: "streamer_dst.md", title: "D/ST streamer (a rule, not a model)" },
  { file: "regression_watch.md", title: "Regression Watch" },
  { file: "decisions_wp.md", title: "Decision Report Card (the win-probability model and its grading)" },
  { file: "hot_seat.md", title: "Hot-Seat Meter" },
  { file: "board_cliff.md", title: "Cliff board (with the Breakout research note)" },
  { file: "questionable.md", title: "Questionable outcomes" },
  { file: "startsit.md", title: "Start/sit odds (the owner's local report only, not on this site)" },
  { file: "teammate_out.md", title: "Teammate out (who gains when a starter sits)" },
] as const;

export const MODEL_CARDS_INDEX = "docs/model_cards/README.md";
