// Method constants: the few numbers the site states that are properties of the method, not
// data. Each one is copied from the Python code named beside it, and
// tests/unit/method.test.ts reads those files and fails when they disagree.

/** The positions the Radar ranks today (web/db/schema.ts radar_list_position_check). The pages
 *  do not use this list for their tabs or cards: those come from the data (lib/positions.ts). */
export const POSITIONS = ["QB", "RB", "WR", "TE"] as const;
export type Position = (typeof POSITIONS)[number];

/** A chance's range is a 90% interval (src/twm/modules/waiver_radar/confidence.py BAND_LEVEL). */
export const CHANCE_RANGE_LEVEL = 0.9;

/** Track-record intervals are 95% season-block bootstrap intervals
 *  (src/twm/backtest/metrics.py LEVEL). */
export const TRACK_INTERVAL_LEVEL = 0.95;

/** Regression Watch's stability study (src/twm/modules/regression_watch/stability.py): a
 *  player-season counts with at least MIN_GAMES games, and its intervals are 95% percentile
 *  intervals (np.nanpercentile 2.5 and 97.5) of N_BOOT bootstrap resamples of player-seasons. */
export const STABILITY_MIN_GAMES = 8;
export const STABILITY_INTERVAL_LEVEL = 0.95;
export const STABILITY_RESAMPLES = 1000;

/** Rank buckets of the hit-rate badges (PROJECT_SPEC 8.1; src/twm/backtest/metrics.py
 *  DEFAULT_BUCKETS). */
export const RANK_BUCKETS: readonly (readonly [number, number])[] = [
  [1, 5],
  [6, 10],
  [11, 25],
];

/** The track record's model keys (reports/waiver_radar/evaluation.csv `model`). */
export const BASELINE_LAST_POINTS = "baseline_last_points";

export const METHOD_NAMES: Record<string, string> = {
  logit: "Waiver Radar (logistic regression)",
  lgbm: "LightGBM (the runner-up model)",
  baseline_last_points: "Last week's points",
  baseline_snap_delta: "Snap-share change",
  baseline_ecr: "FantasyPros experts' ranks",
  base_rate: "A random pick (base rate)",
};

export function methodName(key: string): string {
  return METHOD_NAMES[key] ?? key;
}

export const LABEL_NAMES: Record<string, string> = {
  y_hit: "Hit (at least one starter week)",
  y_sustained: "Sustained hit (at least two starter weeks)",
};

/** The weekly as-of: Tuesday 14:00 UTC (config/settings.yaml as_of.weekly). */
export const AS_OF_WEEKDAY = "Tuesday";
export const AS_OF_TIME_UTC = "14:00";

/** The Decision Report Card (config/settings.yaml `decisions:`; docs/decision_metrics.md). A
 *  decision is graded ("clear") when the best option's WP beats the second best by more than
 *  TOSS_UP_MARGIN (0-1 units: 0.015 = 1.5 WP points); otherwise it is a toss-up. */
export const TOSS_UP_MARGIN = 0.015;
/** Fourth downs with at most this many seconds left in the half are not graded. */
export const END_OF_HALF_SECONDS = 10;
/** Decisions in the last this-many seconds of the 4th quarter (and in overtime) are not graded (G3b: the late-game
 *  win probability is not yet reliable). config/settings.yaml decisions.late_game.q4_seconds. */
export const LATE_GAME_Q4_SECONDS = 120;
/** The coach leaderboard ranks coaches with at least this many games in the season (or, early
 *  in a season, the most games any coach has: src/twm/modules/decisions/decisions_report.py). */
export const LEADERBOARD_MIN_GAMES = 8;
/** The clock-management metrics' thresholds (decisions.clock), fixed before any grading. */
export const CLOCK = {
  oneScoreMargin: 8,
  finalWindowSeconds: 120,
  clockRanMinSeconds: 10,
  passivityMinSeconds: 40,
  passivityMinTimeouts: 1,
  passivityMinEp: 1.0,
} as const;

/** The Hot-Seat Meter (docs/hot_seat.md). The label window: a departure counts when it is announced
 *  no later than this many days after the team's final game (src/twm/modules/hot_seat/targets.py
 *  WINDOW_DAYS). */
export const HOT_SEAT_WINDOW_DAYS = 30;
/** Drivers shown per coach: the logistic regression's largest terms (production.py N_DRIVERS). */
export const HOT_SEAT_DRIVERS = 3;
/** The first weekly list's week (weekly.py FIRST_WEEK): a team needs a played game. */
export const HOT_SEAT_FIRST_WEEK = 2;
/** The early-season check on /hot-seat: season phases (weekly lists by week; the end-of-season
 *  snapshot apart) and probability bands (lower edges, 0-1). Presentation choices of the site. */
export const HOT_SEAT_PHASES = [
  { key: "weeks_early", from: HOT_SEAT_FIRST_WEEK, to: 4 },
  { key: "weeks_mid", from: 5, to: 9 },
  { key: "weeks_late", from: 10, to: null },
  { key: "end_of_season", from: null, to: null },
] as const;
export const HOT_SEAT_BANDS = [0, 0.1, 0.25, 0.5] as const;
/** Step H5's primary estimate (reports/hot_seat/research.csv, spec primary): the odds ratio of
 *  firing per standard deviation of fourth-down WP lost per game, with its classical and
 *  season-bootstrap 95% intervals and the within-season permutation p-value; and the same
 *  model's odds ratio for wins vs market expectation. Checked against the CSV by the unit test. */
export const HOT_SEAT_RESEARCH = {
  oddsRatio: 1.010865,
  classicalLo: 0.757635,
  classicalHi: 1.348733,
  bootstrapLo: 0.716695,
  bootstrapHi: 1.391814,
  permP: 0.938062,
  coachSeasons: 597,
  positiveCoachSeasons: 86,
  seasons: 20,
  winsVsExpectedOddsRatio: 0.307077,
} as const;

/** The Cliff board (docs/board.md). Who is on it: at least BOARD_MIN_PRIOR seasons in the league
 *  before last season and top-BOARD_TOP_PPG at his position in points per game, ranked among
 *  players with at least BOARD_RANK_MIN_GAMES games (src/twm/modules/board/populations.py
 *  CLIFF_MIN_PRIOR, CLIFF_TOP; seasons.py MIN_GAMES_RANKED). The Cliff: BOARD_MIN_GAMES+ games next
 *  season and a drop of at least BOARD_CLIFF_DROP in points per game; missed time: fewer than
 *  BOARD_MIN_GAMES games (populations.py CLIFF_DROP, MIN_GAMES_NEXT). */
export const BOARD_MIN_PRIOR = 3;
export const BOARD_TOP_PPG = 36;
export const BOARD_RANK_MIN_GAMES = 8;
export const BOARD_CLIFF_DROP = 0.3;
export const BOARD_MIN_GAMES = 6;
/** Drivers per chance (board/production.py N_DRIVERS). */
export const BOARD_DRIVERS = 3;
/** The board's as-of: this many hours before the first week-1 kickoff (board/preseason.py
 *  KICKOFF_EVE_LEAD). */
export const BOARD_KICKOFF_LEAD_HOURS = 1;
/** The disagreement record compares each side's top this-many per board (board/evaluation.py
 *  disagreements `top`); the page's marker uses the same size. */
export const BOARD_DISAGREE_TOP = 10;
/** The first board with FantasyPros' preseason expert ranks (evaluation.py ECR_FIRST_SNAPSHOT + 1). */
export const BOARD_ECR_FIRST_SEASON = 2020;
/** The calibration bands on /board (lower edges, 0-1). A presentation choice of the site. */
export const BOARD_BANDS = [0, 0.1, 0.25, 0.5] as const;
