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
