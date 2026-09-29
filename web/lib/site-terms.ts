// Terms the site uses that the feature/metric registry (src/twm/registry.py, published as
// the `glossary` table) does not define: they describe how the SITE presents the Radar
// rather than a feature, label or metric of the pipeline. Worded from docs/waiver_radar.md
// ("How to read the list", "Precision@10", "The weekly list"). No numbers here: numbers come
// from the database. The glossary table wins when it has an entry of the same name.
export type TermEntry = { name: string; title: string; explanation: string; formula?: string };

export const SITE_TERMS: Record<string, TermEntry> = {
  chance: {
    name: "chance",
    title: "Chance",
    explanation:
      "How often players the Radar rated like him became a fantasy starter soon, in earlier " +
      "seasons' backtests. The range beside it is how sure that rate is. It is the track " +
      "record of similar players, not a promise, and not the model's own probability (which " +
      "ran too high for the top players in the backtest).",
  },
  model_probability: {
    name: "model_probability",
    title: "Model probability",
    explanation:
      "The model's own calibrated probability. The site shows the chance instead: in the " +
      "backtest the model's probabilities ran too high for the most likely players.",
  },
  priority: {
    name: "priority",
    title: "Suggested priority",
    explanation:
      "A suggestion from the chance: must-add, speculative or watch. The cutoffs, and how " +
      "often each priority hit in the backtest, are on the Methodology page.",
  },
  list_kind: {
    name: "list_kind",
    title: "Live or reconstructed",
    explanation:
      "A live list was made in real time on the Tuesday and is never changed afterwards. A " +
      "reconstructed (backtest) list was made later from the data as it stood on that " +
      "Tuesday: what the Radar would have said then, not a list anyone saw at the time.",
  },
  precision_at_10: {
    name: "precision_at_10",
    title: "Precision@10",
    explanation:
      "The share of a list's top 10 who became a fantasy starter soon (a hit). If you had " +
      "picked up the top 10 at a position that week, it is the share that would have given " +
      "you a starter week. The track record averages it over every weekly list.",
  },
  rank_bucket_hit_rate: {
    name: "rank_bucket_hit_rate",
    title: "Hit rate by rank",
    explanation:
      "How often players ranked this high hit, counted over earlier reconstructed lists of " +
      "the same position (seasons before this list's season, so the badge never uses " +
      "outcomes the list could not have known).",
  },
  interval: {
    name: "interval",
    title: "Interval",
    explanation:
      "The range a number would plausibly move within if the same kind of seasons were " +
      "played again. It comes from a season-block bootstrap: whole seasons are redrawn at " +
      "random many times and the number is recomputed each time.",
  },
  calibration: {
    name: "calibration",
    title: "Calibration",
    explanation:
      "Whether probabilities mean what they say: among all players given a similar " +
      "probability, the share who really hit should be close to that probability.",
  },
  walk_forward: {
    name: "walk_forward",
    title: "Walk-forward backtest",
    explanation:
      "Grading a model the honest way: every season is predicted by a model trained only on " +
      "the seasons before it, using only data that was public at each Tuesday's as-of time.",
  },
  flex: {
    name: "flex",
    title: "FLEX",
    explanation:
      "A week's running back, wide receiver and tight end lists merged into one and ordered by " +
      "chance. Each chance is the chance of a starter finish at the player's own position, so " +
      "FLEX compares three slightly different targets: it is a way to browse the three lists " +
      "together, not a separate model.",
  },
  listed_position: {
    name: "listed_position",
    title: "Listed position",
    explanation:
      "The position nflverse lists the player at today, also on his past weekly rows. A " +
      "Radar list ranks each player at the position of his team's roster that week, which " +
      "can differ for a player who changed position.",
  },
  current_franchise: {
    name: "current_franchise",
    title: "Team",
    explanation:
      "Teams are shown by today's franchise code and name, also for past seasons: a " +
      "franchise that moved appears under its current name.",
  },
};
