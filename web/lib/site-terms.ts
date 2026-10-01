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
  stream_chance: {
    name: "stream_chance",
    title: "Chance (K and D/ST)",
    explanation:
      "How often picks the streamer rated alike scored like a starter the very next week, in " +
      "earlier seasons' backtests: for kickers, picks with a similar model score; for team " +
      "defenses, the rule's pick at the same rank. The range beside it is how sure that rate " +
      "is. A track record, not a promise.",
  },
  stream_pool: {
    name: "stream_pool",
    title: "Streaming pool",
    explanation:
      "The kickers and team defenses who are probably still on waivers: those ranked low both " +
      "by the experts before the season and by points per game so far. It is an estimate, " +
      "the same kind as the Waiver Radar's candidate pool, because past waiver wires are not " +
      "public.",
  },
  garbage_time_view: {
    name: "garbage_time_view",
    title: "With or without garbage time",
    explanation:
      "Points, expected points and points over expected either over every play, or only " +
      "over the plays while the game was still in doubt. Stats piled up once a game is " +
      "decided say little about next week. The projection itself is the same in both views.",
  },
  current_franchise: {
    name: "current_franchise",
    title: "Team",
    explanation:
      "Teams are shown by today's franchise code and name, also for past seasons: a " +
      "franchise that moved appears under its current name.",
  },
  player_season: {
    name: "player_season",
    title: "Player-season",
    explanation:
      "One player's regular season. The stability study counts each season he played enough " +
      "games in once, at the position of most of his games, and splits his games into two halves.",
  },
  stability_interval: {
    name: "stability_interval",
    title: "Interval (stability study)",
    explanation:
      "The range the number would plausibly move within with other players: the player-seasons " +
      "are redrawn at random many times and the number is recomputed each time. The same player " +
      "appears in several seasons, so the true range is a little wider.",
  },
  // The Decision Report Card (docs/decision_metrics.md). The margin and thresholds are stated on
  // the Methodology page (lib/method.ts, checked against config/settings.yaml).
  decisions_graded: {
    name: "decisions_graded",
    title: "Decisions",
    explanation:
      "Every fourth down and every try after a touchdown the Report Card priced: clear calls and " +
      "toss-ups together. Kneels, the half's last seconds and snaps wiped out by a penalty are left out.",
  },
  clear_call: {
    name: "clear_call",
    title: "Clear call",
    explanation:
      "A decision where one option's win probability beat the next best by more than the toss-up " +
      "margin (Methodology page). Only clear calls are graded: a wrong one counts against the coach.",
  },
  toss_up: {
    name: "toss_up",
    title: "Toss-up",
    explanation:
      "A decision whose best two options were within the toss-up margin of each other: the model " +
      "cannot tell them apart with confidence, so it is counted but never graded, whatever the coach chose.",
  },
  wrong_call: {
    name: "wrong_call",
    title: "Wrong call",
    explanation: "A clear call where the coach did not choose the option with the highest win probability.",
  },
  clock_case: {
    name: "clock_case",
    title: "Clock case",
    explanation:
      "A game where one of the three clock-management metrics applies: timeouts unused in a lost " +
      "one-score game, a passive end of the first half, or seconds wasted late while trailing with " +
      "timeouts in hand. Each has an exact written definition; situations outside them are never graded.",
  },
  against_convention: {
    name: "against_convention",
    title: "Against convention",
    explanation:
      "A clear call where the aggressive option was best and the coach took it: he went for it on " +
      "fourth down, or went for two. The number is how much win probability that gained over the " +
      "best kicking option, by the model.",
  },
  wp_points: {
    name: "wp_points",
    title: "WP points",
    explanation:
      "Win probability in percentage points: one WP point is one percentage point of the team's " +
      "chance to win, by our win-probability model.",
  },
};
