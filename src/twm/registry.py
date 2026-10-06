"""Feature and metric registry (PROJECT_SPEC 11 B6): one entry per number the app shows or feeds
to a model, with its formula, a beginner-friendly explanation and its unit.

Uses:
- UI tooltips and the glossary (`twm glossary`, docs/glossary.md and the site's tooltip
  fallback web/lib/glossary-fallback.json, both generated from this file);
- the Waiver Radar's plain-English reasons (step C6 fills each entry's ``reason_template``);
- :func:`check_features`, the guard every model calls on its feature columns: each must be a
  registered, implemented feature and never an identifier (spec 6.2 rule 5: no player, coach or
  team ids as features).

Rules for entries: formulas describe what the code or nflverse actually computes, verified on the
data where a claim is checkable (the ``verified`` note says how); planned entries say which step
builds them and cannot be used as features until their status is "available".
"""

from __future__ import annotations

import difflib
import json
import re
import string
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from twm.config import FANTASY_POSITIONS, POSITION_WORDS, league, or_join, rank_groups
from twm.situations import SituationRules

Kind = Literal["metric", "feature", "label", "concept", "identifier"]
Status = Literal["available", "planned"]
MODULES = (
    "shared",
    "waiver_radar",
    "streamer",
    "regression_watch",
    "my_league",
    "decisions",
    "hot_seat",
    "board",
    "questionable",
    "teammate_out",
    "playoff_planner",
)
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
# Placeholders a reason template may use (C6, twm.modules.waiver_radar.reasons, fills them
# from the player's own point-in-time feature row): value = the feature's value, prev / delta =
# the earlier value and the change, weeks = games in the window, teammate / why = an
# unavailable teammate's name and why he is out (from teammates_out), team, pos = position.
REASON_FIELDS = ("player", "value", "prev", "delta", "weeks", "teammate", "team", "why", "pos")


class FeatureCheckError(ValueError):
    """A model was given a column that is not a usable, registered feature."""


@dataclass(frozen=True)
class Entry:
    """One registered feature, metric, label, concept or identifier.

    name: snake_case key (the column name when the entry is a column).
    title: short human title. kind: what it is. modules: where it is used.
    unit: e.g. "points", "share (0-1)", "boolean". formula: how it is computed, precisely.
    explanation: one to three plain sentences for a beginner. source: tables/columns or code.
    status: "available" (the code or column exists now) or "planned" (built in ``step``).
    model_output: depends on nflverse model columns (spec 6.3 caveat).
    verified: how a formula claim was checked against the data (empty if nothing to check).
    reason_template: plain-English sentence with {placeholders} from REASON_FIELDS (for C6);
    for a yes/no feature it describes "yes".
    reason_if_false: the sentence for "no" (a yes/no feature whose "no" can help, e.g. no bye
    week coming); None when "no" is never phrased as a reason.
    """

    name: str
    title: str
    kind: Kind
    modules: tuple[str, ...]
    unit: str
    formula: str
    explanation: str
    source: str = ""
    status: Status = "available"
    step: str = ""
    model_output: bool = False
    verified: str = ""
    reason_template: str | None = None
    reason_if_false: str | None = None

    def __post_init__(self) -> None:
        problems = []
        if not _NAME.match(self.name):
            problems.append("name must be snake_case")
        if not self.title or not self.explanation.strip() or not self.formula.strip():
            problems.append("title, formula and explanation are required")
        if not self.modules or not set(self.modules) <= set(MODULES):
            problems.append(f"modules must be a non-empty subset of {MODULES}")
        if self.status == "planned" and not self.step:
            problems.append("a planned entry must name the step that builds it")
        for text in (self.reason_template, self.reason_if_false):
            if text is None:
                continue
            fields = {f for _, f, _, _ in string.Formatter().parse(text) if f}
            unknown = {f.split(".")[0].split("[")[0] for f in fields} - set(REASON_FIELDS)
            if unknown:
                problems.append(f"reason_template uses unknown fields {sorted(unknown)}")
        if self.reason_if_false is not None and self.reason_template is None:
            problems.append("reason_if_false needs a reason_template")
        if problems:
            raise ValueError(f"registry entry {self.name!r}: {'; '.join(problems)}")

    def reason(self, **values: object) -> str:
        """Fill the reason template: ``get("offense_snap_share").reason(player=..., ...)``."""
        if self.reason_template is None:
            raise ValueError(f"{self.name} has no reason template")
        return self.reason_template.format(**values)

    def tooltip(self) -> str:
        return f"{self.title} ({self.unit}): {self.explanation}"


def _pool_texts() -> dict[str, str]:
    """Config-derived pieces of the candidate-pool entries (never hard-coded numbers)."""
    lg = league()
    cut = lg.candidate_pool_cutoffs()
    return {
        "teams": str(lg.teams),
        "cutoffs": ", ".join(f"{p} {n}" for p, n in cut.items()),
        "multiplier": f"{lg.candidate_pool_multiplier:g}",
        "statuses": ", ".join(lg.pool.roster_statuses),
        "min_games": str(lg.pool.prior_season_min_games),
        "rounds": str(lg.pool.rookie_drafted_rounds),
        "below": f"{lg.pool.ownership_available_below:g}",
    }


def _label_texts() -> dict[str, str]:
    """Config-derived pieces of the label entries (C2)."""
    from twm.modules.waiver_radar.labels import MIN_TRAIN_GAMES, WINDOW_GAMES

    lg = league()
    flex = lg.flex_worthy_ranks()
    others = [p for p in FANTASY_POSITIONS if p not in flex]
    return {
        "teams": str(lg.teams),
        "thresholds": ", ".join(f"{p} top {n}" for p, n in lg.starter_thresholds().items()),
        # "RB/WR top 36" (grouped by rank); "none" when the lineup has no FLEX-type slot
        "flex": rank_groups(flex) or "none (no multi-position slot)",
        "flex_positions": "/".join(flex) or "none",
        "flex_players": or_join([f"a {p}" if i == 0 else p for i, p in enumerate(flex)]),
        "flex_words": or_join([POSITION_WORDS[p] for p in flex]),
        "flex_never": or_join(others, "and"),
        "window": str(WINDOW_GAMES),
        "min_games": str(MIN_TRAIN_GAMES),
    }


# Plain-English reasons of the weekly Waiver Radar list (step C6): feature -> (sentence for the
# value, sentence for "no" of a yes/no feature). Written for a first-season fantasy player, in
# the words of the list ("he" is the player on that row). Filled by
# twm.modules.waiver_radar.reasons from the player's own feature row at the as-of. `position`
# has none on purpose: it moves a whole position list alike and never explains a ranking
# within it. A feature without a sentence falls back to "<title>: <value>".
_REASONS: dict[str, tuple[str, str | None]] = {
    "snap_share_last": ("Played {value:.0%} of his team's snaps last game", None),
    "snap_share_avg3": ("Played {value:.0%} of his team's snaps over the last {weeks} games",
                        None),
    "snap_share_season": ("Has played {value:.0%} of his team's snaps this season", None),
    "snap_share_delta": ("Snap share rose from {prev:.0%} to {value:.0%} in his last game", None),
    "target_share_last": ("Drew {value:.0%} of his team's targets last game", None),
    "target_share_avg3": ("Drew {value:.0%} of his team's targets over the last {weeks} games",
                          None),
    "air_yards_share_avg3": ("Got {value:.0%} of his team's air yards (throws down the field) "
                             "over the last {weeks} games", None),
    "wopr_avg3": ("Receiving workload (WOPR: targets and air yards combined) of {value:.2f} "
                  "over the last {weeks} games", None),
    "carry_share_last": ("Got {value:.0%} of his team's carries last game", None),
    "carry_share_avg3": ("Got {value:.0%} of his team's carries over the last {weeks} games",
                         None),
    "rz_targets_avg3": ("Drew {value:.1f} targets per game inside the opponent's 20-yard line "
                        "over the last {weeks} games", None),
    "rz_carries_avg3": ("Got {value:.1f} carries per game inside the opponent's 20-yard line "
                        "over the last {weeks} games", None),
    "gl_opps_avg3": ("Had {value:.1f} chances per game inside the opponent's 10-yard line over "
                     "the last {weeks} games", None),
    "routes_proxy_avg3": ("Was on the field for about {value:.0f} pass plays per game over the "
                          "last {weeks} games", None),
    "xfp_last": ("His targets and carries last game were worth {value:.1f} fantasy points for "
                 "an average player", None),
    "xfp_avg3": ("His targets and carries were worth {value:.1f} fantasy points per game over "
                 "the last {weeks} games", None),
    "fpoe_avg3": ("Scored {value:.1f} points per game more than his chances were worth over "
                  "the last {weeks} games", None),
    "fantasy_points_last": ("Scored {value:.1f} fantasy points last game", None),
    "fantasy_points_avg3": ("Averaged {value:.1f} fantasy points over the last {weeks} games",
                            None),
    "ppg_to_date": ("Averages {value:.1f} fantasy points per game this season", None),
    "ppg_pos_rank": ("Ranks No. {value:.0f} among {pos}s in points per game this season", None),
    "preseason_pos_rank": ("Was ranked No. {value:.0f} among {pos}s before the season", None),
    "preseason_ranked": ("Was on a preseason ranking list", None),
    "games_played_to_date": ("Has played in {value:.0f} games this season", None),
    "depth_rank_now": ("Is No. {value:.0f} at {pos} on his team's depth chart", None),
    "depth_rank_prev": ("Was No. {value:.0f} at {pos} on his team's depth chart a week earlier",
                        None),
    "depth_rank_change": ("Moved up the depth chart from No. {prev:.0f} to No. {value:.0f} at "
                          "{pos}", None),
    "depth_listed": ("Is listed on his team's depth chart", None),
    "vacated_target_share": ("{teammate} {why}: {value:.0%} of the team's targets are up for "
                             "grabs", None),
    "vacated_carry_share": ("{teammate} {why}: {value:.0%} of the team's carries are up for "
                            "grabs", None),
    "same_pos_vacated_target_share": ("{teammate} ({pos}) {why}: {value:.0%} of the team's "
                                      "targets are up for grabs at his position", None),
    "same_pos_vacated_carry_share": ("{teammate} ({pos}) {why}: {value:.0%} of the team's "
                                     "carries are up for grabs at his position", None),
    "teammate_same_pos_unavailable": ("{value:.0f} {pos} teammate(s) are out: {teammate}", None),
    "top_teammate_out": ("{teammate}, who played more snaps than him, {why}", None),
    "joined_team_recently": ("Joined {team} during the season", None),
    "team_epa_per_play": ("His offense ({team}) has been efficient this season: {value:+.2f} "
                          "expected points added per play", None),
    "team_epa_per_play_neutral": ("His offense ({team}) has been efficient when the game is "
                                  "close: {value:+.2f} expected points added per play", None),
    "team_plays_per_game": ("His team ({team}) runs {value:.0f} plays per game: more plays, "
                            "more chances", None),
    "team_pass_rate_neutral": ("His team ({team}) passes on {value:.0%} of its plays when the "
                               "game is close", None),
    "opp_fp_allowed_next3": ("Soft schedule: his next opponents have allowed {value:.2f} times "
                             "the average fantasy points to {pos}s", None),
    "bye_in_next3": ("Has a bye week in the next 3 weeks", "No bye week in the next 3 weeks"),
    "team_games_remaining": ("His team has {value:.0f} games left this season", None),
    "age_at_asof": ("Is {value:.0f} years old: younger players more often grow into a bigger "
                    "role", None),
    "is_rookie": ("Is a rookie: first-year players often earn more snaps as the season goes on",
                  None),
    "draft_round": ("Was drafted in round {value:.0f}: teams give higher draft picks more "
                    "chances", None),
    "is_undrafted": ("Went undrafted", "Was drafted: teams give drafted players more chances"),
    "years_exp": ("Has {value:.0f} seasons of NFL experience", None),
}  # fmt: skip


def _feature_entries() -> list[Entry]:
    """The Waiver Radar model features of step C3 (twm.modules.waiver_radar.features), one
    entry per column. Windows: 'last' = the team's most recent game visible at the as-of,
    'avg3' = its last 3, 'season' = all of them; a game the player missed counts as 0."""
    from twm.modules.waiver_radar.features import FeatureRules

    r = FeatureRules.from_config()
    win = (
        "team games = regular-season games of the player's as-of team visible at the as-of; "
        "last = the most recent, avg3 = mean over the last "
        f"{r.window} (fewer early in the season), season = mean over all; a game he missed "
        "counts as 0; NULL only when the team has no visible game"
    )
    feat = "twm.modules.waiver_radar.features"
    rows: list[tuple[str, str, str, str, str, str, str | None]] = [
        # name, title, unit, formula, explanation, source, reason template
        ("snap_share_last", "Snap share, last game", "share (0-1)",
         f"offense_snap_share in the team's last game ({win})",
         "How much he played in the most recent game. A jump is often the first sign of a "
         "bigger role.", "fact_snaps.offense_pct",
         None),
        ("snap_share_avg3", "Snap share, last 3 games", "share (0-1)",
         f"mean offense_snap_share over the team's last 3 games ({win})",
         "His usual playing time lately, less noisy than one game.", "fact_snaps.offense_pct",
         None),
        ("snap_share_season", "Snap share, season", "share (0-1)",
         f"mean offense_snap_share over all team games of the season ({win})",
         "His playing time over the whole season so far.", "fact_snaps.offense_pct", None),
        ("snap_share_delta", "Snap share change", "share points",
         "snap_share_last minus the mean snap share of the up to 3 team games before the last "
         "(NULL when the last game is the team's first)",
         "Did his playing time just go up or down? A big rise means the coaches trust him "
         "more.", "fact_snaps.offense_pct",
         None),
        ("target_share_last", "Target share, last game", "share (0-1)",
         f"nflverse target_share in the team's last game, 0 without a stat line ({win})",
         "The share of his team's passes thrown his way last game.",
         "fact_player_week.target_share", None),
        ("target_share_avg3", "Target share, last 3 games", "share (0-1)",
         f"mean nflverse target_share over the team's last 3 games ({win})",
         "How much of the passing game goes to him lately.", "fact_player_week.target_share",
         None),
        ("air_yards_share_avg3", "Air-yards share, last 3 games", "share (0-1)",
         f"mean nflverse air_yards_share over the team's last 3 games ({win}); can be "
         "slightly negative (passes behind the line)",
         "How much of the team's downfield passing is aimed at him.",
         "fact_player_week.air_yards_share", None),
        ("wopr_avg3", "WOPR, last 3 games", "index (about 0-1)",
         f"mean nflverse wopr (1.5 x target share + 0.7 x air-yards share) over the team's "
         f"last 3 games ({win})", "One number for a receiver's recent opportunity.",
         "fact_player_week.wopr", None),
        ("carry_share_last", "Carry share, last game", "share (0-1)",
         f"carry_share in the team's last game ({win})",
         "The share of his team's runs he got last game.",
         "fact_player_week.carries, fact_team_week.carries",
         None),
        ("carry_share_avg3", "Carry share, last 3 games", "share (0-1)",
         f"mean carry_share over the team's last 3 games ({win})",
         "How much of the running game goes to him lately.",
         "fact_player_week.carries, fact_team_week.carries",
         None),
        ("rz_targets_avg3", "Red-zone targets per game", "targets per game",
         f"mean over the team's last 3 games of his targets on pass plays (not two-point "
         f"tries) with yardline_100 <= {r.red_zone_yardline} ({win})",
         "Targets inside the opponent's 20-yard line, where touchdowns come from.",
         "fact_play.receiver_player_id, yardline_100", None),
        ("rz_carries_avg3", "Red-zone carries per game", "carries per game",
         f"mean over the team's last 3 games of his runs (play_type run, not two-point tries; "
         f"kneels excluded) with yardline_100 <= {r.red_zone_yardline} ({win})",
         "Carries inside the opponent's 20-yard line.",
         "fact_play.rusher_player_id, yardline_100", None),
        ("gl_opps_avg3", "Goal-line opportunities per game", "looks per game",
         f"mean over the team's last 3 games of his targets plus carries (as above) with "
         f"yardline_100 <= {r.goal_line_yardline} ({win})",
         "Chances inside the 10-yard line: the most valuable touches in fantasy.",
         "fact_play", None),
        ("routes_proxy_avg3", "Routes proxy", "dropbacks per game",
         f"mean over the team's last 3 games of (team dropbacks in the game x his snap share "
         f"in it); dropbacks = plays with qb_dropback = 1 that are not two-point tries "
         f"(passes, sacks, scrambles) ({win})",
         "About how many pass plays he was on the field for: a stand-in for routes run, "
         "which nflverse does not publish.", "fact_play.qb_dropback, fact_snaps.offense_pct",
         None),
        ("xfp_last", "xFP, last game", "points",
         f"xfp in the team's last game, 0 without a row ({win})",
         "Expected fantasy points from his chances last game.", "fact_opportunity_week", None),
        ("xfp_avg3", "xFP, last 3 games", "points per game",
         f"mean xfp over the team's last 3 games ({win})",
         "What his recent chances were worth per game, whatever he did with them.",
         "fact_opportunity_week",
         None),
        ("fpoe_avg3", "FPOE, last 3 games", "points per game",
         f"mean over the team's last 3 games of (fantasy_points - xfp) ({win})",
         "Scoring above or below his chances lately; mostly luck, so it is weighted low.",
         "fact_player_week, fact_opportunity_week", None),
        ("fantasy_points_last", "Fantasy points, last game", "points",
         f"fantasy points (config/scoring.yaml) in the team's last game ({win})",
         "What he scored last game.", "fact_player_week (twm.scoring.score_sql)",
         None),
        ("fantasy_points_avg3", "Fantasy points, last 3 games", "points per game",
         f"mean fantasy points over the team's last 3 games ({win})",
         "His recent scoring per game.", "fact_player_week (twm.scoring.score_sql)", None),
        ("ppg_pos_rank", "PPG rank at his position", "rank (1 = best)",
         "rank of ppg_to_date within his roster position among roster players with a game "
         "(ties share the better rank; the candidate pool's ppg_pos_rank)",
         "Where his points per game rank at his position so far.",
         "twm.modules.waiver_radar.pool", None),
        ("preseason_ranked", "Ranked before the season", "boolean",
         "preseason_pos_rank is not NULL (FantasyPros ECR 2020 on; last season's PPG rank "
         "or a round 1-2 rookie before)",
         "Whether any preseason list ranked him at all.", "twm.modules.waiver_radar.pool",
         None),
        ("games_played_to_date", "Games played this season", "games",
         "regular-season games with a stat line visible at the as-of (the pool's "
         "games_to_date)", "How many games he has a stat line in so far.",
         "twm.modules.waiver_radar.pool", None),
        ("depth_rank_now", "Depth-chart rank now", "rank (1 = starter)",
         "on his team's depth chart in force at the as-of (daily charts 2025+: the team's "
         "latest snapshot with dt <= as-of; weekly charts: the latest visible week's chart), "
         "1 + the number of players of his position group with a better (lower) depth rank "
         "in any offensive slot of the group; ties share the better rank. Slots map to groups "
         "by twm.modules.waiver_radar.features.slot_group (QB; RB, HB, FB, J; WR, LWR, RWR, "
         "SWR, WR1/WR2, WRE, WE; TE, LTE, RTE, H-B, F and combined slots with TE). NULL when "
         "he is not in a slot of his group",
         "Where the team lists him at his position: 1 is the starter.",
         "fact_depth_chart.position, depth_rank", None),
        ("depth_rank_prev", "Depth-chart rank a week earlier", "rank (1 = starter)",
         f"depth_rank_now computed from the chart in force {r.depth_prev_lag.days} days "
         "before the as-of", "Where he was listed a week ago.",
         "fact_depth_chart", None),
        ("depth_rank_change", "Depth-chart move", "ranks (positive = promoted)",
         "depth_rank_prev - depth_rank_now (NULL unless both exist)",
         "How many spots he moved up the depth chart in a week.", "fact_depth_chart",
         None),
        ("depth_listed", "On the depth chart", "boolean",
         "his gsis_id is on his team's chart in force at the as-of in any slot (offense, "
         "defense or special teams); NULL when the team has no visible chart",
         "Whether the team lists him at all.", "fact_depth_chart", None),
        ("vacated_target_share", "Vacated target share", "share (sum)",
         "sum over his unavailable teammates (see teammate_unavailable) of their "
         "target_share averaged over the team's last 3 games up to their last appearance "
         "(before they became unavailable; games they missed count as 0)",
         "Targets that teammates who are now out used to get: somebody has to catch them.",
         "fact_player_week, fact_roster_week, fact_injury_report, fact_snaps",
         None),
        ("vacated_carry_share", "Vacated carry share", "share (sum)",
         "as vacated_target_share, with carry_share", "Carries freed up by teammates who "
         "are out.", "fact_player_week, fact_team_week, fact_roster_week",
         None),
        ("same_pos_vacated_target_share", "Vacated target share at his position",
         "share (sum)",
         "vacated_target_share over unavailable teammates of his own position group only",
         "Targets freed up by players at his own position: the most direct path to more "
         "work.", "as vacated_target_share",
         None),
        ("same_pos_vacated_carry_share", "Vacated carry share at his position",
         "share (sum)",
         "vacated_carry_share over unavailable teammates of his own position group only",
         "Carries freed up by players at his own position.", "as vacated_carry_share",
         None),
        ("teammate_same_pos_unavailable", "Teammates out at his position", "players",
         "number of unavailable teammates of his position group", "How many players at his "
         "position are out.", "as vacated_target_share", None),
        ("top_teammate_out", "A player ahead of him is out", "boolean",
         "an unavailable same-position teammate averaged a higher snap share than he did "
         "over the same games (the teammate's last 3 team games up to his last appearance)",
         "Someone who played ahead of him is out: the classic waiver opportunity.",
         "fact_snaps, as vacated_target_share",
         None),
        ("joined_team_recently", "New team this season", "boolean",
         "his latest visible roster team differs from his earliest roster team this season",
         "He changed teams during the season (trade or signing).", "fact_roster_week", None),
        ("team_epa_per_play", "Team EPA per play", "points per play",
         "mean epa over his team's run and pass plays (no two-point tries) in the season's "
         "visible games", "How efficient his offense is: good offenses create more points to "
         "go around.", "fact_play.epa", None),
        ("team_epa_per_play_neutral", "Team EPA per play (neutral)", "points per play",
         "as team_epa_per_play, neutral situations only (fact_play.is_neutral)",
         "Offensive efficiency when the game is close, the fairest view of a team.",
         "fact_play.epa, is_neutral", None),
        ("team_plays_per_game", "Team plays per game", "plays per game",
         "his team's run and pass plays (no two-point tries) / its visible games",
         "Pace: more plays mean more chances for everybody.", "fact_play", None),
        ("team_pass_rate_neutral", "Team neutral pass rate", "share (0-1)",
         "share of his team's neutral-situation run and pass plays with pass = 1 (passes, "
         "sacks, scrambles)", "How pass-heavy his team is when the score does not force it.",
         "fact_play.pass, is_neutral", None),
        ("opp_fp_allowed_next3", "Next opponents' points allowed", "ratio (1 = average)",
         "mean over his team's next 3 scheduled opponents (fact_schedule weeks after N, byes "
         "skipped; the listed cancelled games count until played) of: fantasy points the "
         "opponent's defense allowed per game to his position group this season (visible "
         "games; the scorer's snap-count position of that game, else his latest roster "
         "position) / the league average per team-game for the group; an opponent without a "
         "visible game counts 1.0; NULL when his team has no game left. No betting lines "
         "(spec 6.4)", "Whether his next matchups are soft (above 1) or tough (below 1) for "
         "his position.", "fact_schedule, fact_player_week, fact_snaps",
         None),
        ("n_opp_games_seen", "Opponent games observed", "games",
         "sum of the visible games of the next opponents used by opp_fp_allowed_next3",
         "How much evidence the matchup number rests on (little early in the season).",
         "fact_game", None),
        ("bye_in_next3", "Bye in the next 3 weeks", "boolean",
         "his team has no scheduled game in at least one of the calendar weeks N+1 to N+3 "
         "(capped at the last regular-season week)", "A bye costs a week of production.",
         "fact_schedule, dim_week", None),
        ("team_games_remaining", "Games remaining", "games",
         "his team's regular-season fixtures after week N (fact_schedule, plus listed "
         "cancelled games)", "How much season is left to use him.", "fact_schedule", None),
        ("position", "Position", "category (QB, RB, WR, TE)",
         "his point-in-time roster position (the pool's position)",
         "Which position he plays; hit rates differ by position. A category, not an "
         "identifier.", "fact_roster_week.position", None),
        ("age_at_asof", "Age", "years",
         "(as-of date - dim_player.birth_date) / 365.25; NULL without a public birth date",
         "Younger players are likelier to grow into a bigger role.", "dim_player.birth_date",
         None),
        ("is_rookie", "Rookie", "boolean",
         "his latest visible roster row's entry_year equals the season",
         "First-year players often earn more snaps as the season goes on.",
         "fact_roster_week.entry_year", None),
        ("draft_round", "Draft round", "round (1-7)",
         "dim_player.draft_round; NULL when undrafted (see is_undrafted)",
         "Teams give early picks more chances.", "dim_player.draft_round", None),
        ("is_undrafted", "Undrafted", "boolean",
         "no draft round in dim_player at the as-of", "He was not drafted.",
         "dim_player.draft_round", None),
        ("years_exp", "Years of experience", "seasons",
         "his latest visible roster row's years_exp", "Seasons in the league before this one.",
         "fact_roster_week.years_exp", None),
    ]  # fmt: skip
    return [
        Entry(
            name=name,
            title=title,
            kind="feature",
            modules=("waiver_radar",),
            unit=unit,
            formula=formula,
            explanation=explanation,
            source=f"{feat}; {source}",
            step="C3",
            model_output=name in ("xfp_last", "xfp_avg3", "fpoe_avg3")
            or name.startswith("team_epa"),
            reason_template=_REASONS[name][0] if name in _REASONS else template,
            reason_if_false=_REASONS[name][1] if name in _REASONS else None,
        )
        for name, title, unit, formula, explanation, source, template in rows
    ] + [
        Entry(
            name="teammate_unavailable",
            title="Unavailable teammate",
            kind="concept",
            modules=("waiver_radar",),
            unit="rule that fired",
            formula="a teammate (same as-of team, QB/RB/WR/TE, who played for the team this "
            "season) is unavailable at the as-of if ANY of: roster_status = his row on the "
            "team's latest visible weekly roster has a status other than "
            f"{', '.join(r.available_statuses)} (RES, PUP, SUS, CUT ...), used only in "
            "seasons whose roster statuses change from week to week (2016 on: the 2002-2015 "
            "rosters repeat one, season-final status on every week); left_team = he was "
            "on the team's roster earlier this season but not on its latest visible roster "
            "(released or traded); injury_report = the team's latest visible injury report of "
            f"the season lists him {' or '.join(r.injury_statuses)}; missed_last_game = no "
            "offensive snap or stat line in the team's last game after averaging at least "
            f"{r.missed_game_min_snap_share:.0%} snap share in the up to {r.window} team games "
            "before it",
            explanation="A teammate who is out now: his targets and carries are up for grabs. "
            "Each rule is recorded so the app can say why.",
            source=f"{feat} (fact_roster_week, fact_injury_report, fact_snaps)",
            step="C3",
        ),
    ]


def _regression_entries() -> list[Entry]:
    """The Regression Watch frame's metrics (D1: twm.modules.regression_watch.player_week).
    Metrics, not features: no model reads them yet (D2/D3 will register the features they use).
    """
    frame = "twm.modules.regression_watch.player_week"
    gt = "garbage-time plays (fact_play.is_garbage_time)"
    same_row = "the same ffopportunity row (fact_opportunity_week), i.e. over the same plays"
    rows = [
        ("points_ng", "Fantasy points without garbage time", "points",
         f"fantasy_points - points_garbage: the weekly stat line's points minus those scored on "
         f"his {gt}",
         "The points he scored while the game was still in doubt. Late points in a blowout "
         "come against soft defenses and say little about next week.", True),
        ("xfp_ng", "xFP without garbage time", "points",
         "xfp - xfp_garbage: the weekly xFP minus the expected points of his garbage-time "
         "targets, carries and passes (the own walk-forward xFP's in Regression Watch and on "
         "the player pages)",
         "What his chances were worth while the game was still in doubt.", True),
        ("fpoe_ng", "FPOE without garbage time", "points",
         "points_ng - xfp_ng",
         "Points over expected, counting only plays while the game was in doubt.", True),
        ("points_garbage", "Fantasy points in garbage time", "points",
         f"the fantasy points of his {gt}, credited play by play as in play_points",
         "Points he scored after the game was effectively decided.", True),
        ("xfp_garbage", "xFP in garbage time", "points",
         f"the expected points of his {gt}, counted play by play as in play_xfp (NULL "
         "without an ffopportunity row)",
         "What his chances after the game was decided were worth.", True),
        ("play_points", "Fantasy points added up play by play", "points",
         "the sum over his plays of each play's stats scored with config/scoring.yaml "
         "(twm.scoring.score_sql): passer = passing yards, passing touchdown, interception; "
         "target = reception, receiving yards; rusher = rushing yards; lateral receiver or "
         "rusher = the lateral's yards; td_player_id = the touchdown (rushing, receiving, return "
         "or fumble-recovery); two-point conversions to the passer, target or rusher of the "
         "try; a lost fumble to the player who lost it",
         "The same points as the weekly stat line, credited play by play so a game can be "
         "split into parts; equal to fantasy_points for 99.9% of player-games.", False),
        ("play_xfp", "xFP added up play by play", "points",
         "the sum over his plays of ffopportunity's per-play expected stats scored like xfp: a "
         "pass is worth pass_completion_exp catches (completions for the passer) and "
         "pass_completion_exp x (air_yards + yards_after_catch_exp) yards plus "
         "pass_touchdown_exp (and, for the passer, pass_interception_exp); a run "
         "rush_yards_exp and rush_touchdown_exp; a two-point try only two_point_conv_exp",
         "The same expected points as the weekly xFP, counted play by play (equal within "
         "rounding).", True),
        ("n_opportunities", "Opportunities", "plays",
         "targets + carries + pass attempts (two-point tries included, sacks not) with an "
         "ffopportunity per-play row",
         "How many chances he had in the game.", False),
        ("n_opportunities_garbage", "Opportunities in garbage time", "plays",
         f"n_opportunities on {gt}", "How many of his chances came after the game was "
         "decided.", True),
    ]  # fmt: skip
    comps = [
        ("targets", "Targets", "targets", "rec_attempt: passes thrown to him (two-point tries "
         "excluded)"),
        ("receptions", "Receptions", "catches", "receptions"),
        ("receptions_exp", "Expected receptions", "catches", "receptions_exp: the sum of the "
         "completion chances of his targets"),
        ("receiving_yards", "Receiving yards", "yards", "rec_yards_gained"),
        ("receiving_yards_exp", "Expected receiving yards", "yards", "rec_yards_gained_exp"),
        ("receiving_tds", "Receiving touchdowns", "touchdowns", "rec_touchdown"),
        ("receiving_tds_exp", "Expected receiving touchdowns", "touchdowns",
         "rec_touchdown_exp"),
        ("carries", "Carries", "carries", "rush_attempt (two-point tries excluded)"),
        ("rushing_yards", "Rushing yards", "yards", "rush_yards_gained"),
        ("rushing_yards_exp", "Expected rushing yards", "yards", "rush_yards_gained_exp"),
        ("rushing_tds", "Rushing touchdowns", "touchdowns", "rush_touchdown"),
        ("rushing_tds_exp", "Expected rushing touchdowns", "touchdowns", "rush_touchdown_exp"),
        ("pass_attempts", "Pass attempts", "passes", "pass_attempt (sacks and two-point tries "
         "excluded)"),
        ("completions", "Completions", "passes", "pass_completions"),
        ("completions_exp", "Expected completions", "passes", "pass_completions_exp"),
        ("passing_yards", "Passing yards", "yards", "pass_yards_gained"),
        ("passing_yards_exp", "Expected passing yards", "yards", "pass_yards_gained_exp"),
        ("passing_tds", "Passing touchdowns", "touchdowns", "pass_touchdown"),
        ("passing_tds_exp", "Expected passing touchdowns", "touchdowns", "pass_touchdown_exp"),
        ("interceptions", "Interceptions thrown", "interceptions", "pass_interception"),
        ("interceptions_exp", "Expected interceptions", "interceptions",
         "pass_interception_exp"),
    ]  # fmt: skip
    out = [
        Entry(
            name=name,
            title=title,
            kind="metric",
            modules=("regression_watch",),
            unit=unit,
            formula=formula,
            explanation=explanation,
            source=frame,
            step="D1",
            model_output=model,
        )
        for name, title, unit, formula, explanation, model in rows
    ]
    for name, title, unit, column in comps:
        expected = name.endswith("_exp")
        out.append(
            Entry(
                name=name,
                title=title,
                kind="metric",
                modules=("regression_watch",),
                unit=unit,
                formula=f"fact_opportunity_week.{column}, from {same_row}",
                explanation=(
                    "What an average player would have produced from the same chances; "
                    "compare with the actual number to see efficiency."
                    if expected
                    else "The actual number in the game, counted by ffopportunity over the "
                    "same plays as its expected value."
                ),
                source=f"{frame} (fact_opportunity_week)",
                step="D1",
                model_output=expected,
            )
        )
    out += [
        Entry(
            name="yac",
            title="Yards after the catch",
            kind="metric",
            modules=("regression_watch",),
            unit="yards",
            formula="receiving_yards - air_yards over his caught targets (two-point tries "
            "excluded), from fact_opportunity_pass",
            explanation="Yards he gained with the ball after catching it.",
            source=f"{frame} (fact_opportunity_pass)",
            step="D1",
        ),
        Entry(
            name="yac_exp",
            title="Expected yards after the catch",
            kind="metric",
            modules=("regression_watch",),
            unit="yards",
            formula="yards_after_catch_exp over his caught targets (two-point tries excluded)",
            explanation="Yards after the catch an average receiver would have gained on the "
            "same catches; yac - yac_exp is 'YAC over expected'.",
            source=f"{frame} (fact_opportunity_pass)",
            step="D1",
            model_output=True,
        ),
    ]
    return out


def _stability_entries() -> list[Entry]:
    """The stability study's metrics (D2: twm.modules.regression_watch.stability)."""
    src = "twm.modules.regression_watch.stability"
    half = "over the games of one half of a player-season (summed, then divided)"
    rows = [
        ("split_half_correlation", "Split-half correlation", "correlation (-1 to 1)",
         "Pearson correlation, across player-seasons (8+ games with a target, carry or pass; "
         "2009 on; one position per player-season), of a metric in one half of his games with "
         "the same metric in the other half: odd against even games, or his first n // 2 "
         "games against the rest",
         "How much a number repeats within a season: near 1 it is a lasting trait (role or "
         "skill), near 0 it was mostly luck.", False),
        ("td_rate_over_expected", "TD rate over expected", "touchdowns per chance",
         "(passing_tds + rushing_tds + receiving_tds - the same three _exp) / (pass_attempts + "
         f"carries + targets), {half}",
         "Touchdowns beyond what his chances were worth, per pass, carry or target.", True),
        ("catch_rate_over_expected", "Catch rate over expected", "catches per target",
         f"(receptions - receptions_exp) / targets, {half}",
         "How many more of his targets he caught than an average receiver would have.", True),
        ("completion_rate_over_expected", "Completion rate over expected (CPOE)",
         "completions per pass", f"(completions - completions_exp) / pass_attempts, {half}",
         "How many more of his passes were completed than an average passer's would have "
         "been.", True),
        ("yac_over_expected", "YAC over expected", "yards per catch",
         f"(yac - yac_exp) / receptions, {half}",
         "Yards after the catch beyond what an average receiver gains on the same catches.",
         True),
        ("signal_variance", "Signal variance", "points squared",
         "covariance, across player-seasons of a position, of FPOE/game in his odd games and "
         "in his even games",
         "How much players truly differ in the metric once luck is taken out.", True),
        ("noise_variance", "Noise variance per game", "points squared",
         "var(odd-game mean - even-game mean) / mean(1 / odd games + 1 / even games), across "
         "player-seasons of a position",
         "How much a single game's value swings by luck alone.", True),
        ("reliability", "Reliability after g games", "share (0-1)",
         "r(g) = signal_variance / (signal_variance + noise_variance / g)",
         "The share of a g-game average that is real rather than luck; it grows with g.", True),
        ("shrinkage_factor", "Shrinkage factor", "share (0-1)",
         "reliability r(g) of FPOE/game for his position after his g games, estimated only "
         "from seasons before the one projected (stability.shrinkage(seasons))",
         "How much of a player's points over expected to keep when projecting the rest of the "
         "season; the rest is expected to fade.", True),
        ("games_for_half_weight", "Games for half weight", "games",
         "noise_variance / signal_variance: the g where r(g) = 0.5",
         "After this many games a player's FPOE/game deserves half its face value.", True),
        ("prior_mean", "Average FPOE per game (prior)", "points",
         "sum of FPOE over sum of games, over the player-seasons of a position",
         "The value a shrunk FPOE/game is pulled toward when shrinking toward the position "
         "average instead of zero.", True),
    ]  # fmt: skip
    return [
        Entry(
            name=name,
            title=title,
            kind="metric",
            modules=("regression_watch",),
            unit=unit,
            formula=formula,
            explanation=explanation,
            source=src,
            step="D2",
            model_output=model,
        )
        for name, title, unit, formula, explanation, model in rows
    ]


def _projection_entries() -> list[Entry]:
    """The rest-of-season projection and the tags (D3: twm.modules.regression_watch.projection,
    tags). Texts quote the league shape (config/league.yaml)."""
    from twm.modules.regression_watch import projection as pj
    from twm.modules.regression_watch import tags as tg

    lg = league()
    sizes = ", ".join(f"{p} {n}" for p, n in pj.universe_sizes(lg).items())
    starters = ", ".join(f"{p} top {n}" for p, n in lg.starter_thresholds().items())
    grid = " / ".join("none" if h is None else str(h) for h in pj.HALF_LIVES)
    decile = "top (bottom) ceil(n / 10) FPOE/game of his position's universe at that as-of"
    rows = [
        ("regression_universe", "Regression Watch universe", "concept", "players",
         f"QB/RB/WR/TE with at least {pj.MIN_GAMES} games so far whose PPG or xFP/game ranks "
         f"inside teams x (starting slots the position can fill, FLEX included) x "
         f"{lg.candidate_pool_multiplier:g}: {sizes}",
         "The fantasy-relevant players Regression Watch projects and tags each week.", False),
        ("recency_weighted_xfp", "Recency-weighted xFP per game", "metric", "points per game",
         "sum of 0.5 ** (k / h) x xFP over his games this season / sum of 0.5 ** (k / h), k = "
         f"games before his latest one, h = the half-life ({grid} games; chosen per season on "
         "earlier seasons)",
         "His expected points per game, with recent games counting a little more.", True),
        ("ppg_ros", "Rest-of-season projection", "metric", "points per game",
         "recency-weighted xFP/game + r(g) x FPOE/game (spec: shrink toward 0) or m + r(g) x "
         "(FPOE/game - m) (toward the position's average m); r(g) = shrinkage_factor after his "
         "g games with an opportunity, estimated only on seasons before this one; the variant "
         "(target, half-life, garbage time) is chosen on earlier seasons "
         "(docs/regression_watch.md)",
         "The points per game we expect for the rest of the season: his opportunity, plus the "
         "small part of his luck-or-skill surplus that tends to last.", True),
        ("sell_high", "Sell-high", "metric", "yes/no",
         f"FPOE/game in the {decile.replace(' (bottom)', '')} AND ppg_ros at least X "
         "(tag_threshold_x) below his PPG",
         "He has scored well above what his chances were worth, and the projection says it "
         "will fade: a good time to trade him away.", True),
        ("buy_low", "Buy-low", "metric", "yes/no",
         f"FPOE/game in the {decile.replace('top (bottom)', 'bottom')} AND ppg_ros at least X "
         "above his PPG",
         "He has scored well below what his chances were worth: his points should rise, so he "
         "may be cheap to trade for.", True),
        ("legit", "Legit (tested, not shown)", "metric", "yes/no",
         f"tested, not shown: PPG rank inside the starter threshold ({starters}) AND FPOE/game "
         "not in the top decile of his position's universe; D3's backtest only, dropped from "
         "the product on 2026-09-30",
         "Tested, not shown: a starter whose production is carried by his opportunity, not by "
         "luck. It predicted nothing (in the backtest 59.0% of the players it tagged stayed "
         "starters, and so did 61.1% of every starter), so the lists do not carry it.", True),
        ("tag_threshold_x", "Tag threshold X", "concept", "points per game",
         f"per season, the X in 0, 0.5 .. 6 with the best Sell-high (Buy-low) precision on "
         f"the earlier seasons among those tagging at least {tg.MIN_TAGS_PER_ASOF} players per "
         "week; never chosen on the season it is used in",
         "How far the projection must sit from his current PPG before we tag him.", False),
        ("rest_of_season_ppg", "Rest-of-season PPG (actual)", "label", "points per game",
         "fantasy points per game over his regular-season games public after the as-of "
         f"(graded only with at least {pj.MIN_GAMES} such games)",
         "What he really scored per game afterwards: what the projection is graded on.", False),
    ]  # fmt: skip
    return [
        Entry(
            name=name,
            title=title,
            kind=kind,
            modules=("regression_watch",),
            unit=unit,
            formula=formula,
            explanation=explanation,
            source="twm.modules.regression_watch."
            + ("tags" if kind == "metric" and unit == "yes/no" else "projection"),
            step="D3",
            model_output=model,
        )  # fmt: skip
        for name, title, kind, unit, formula, explanation, model in rows
    ] + [_range_entry()]


def _range_entry() -> Entry:
    """The projection's 80% range (feature #4: twm.modules.regression_watch.ranges)."""
    from twm.modules.regression_watch import ranges as rg

    tail = round((1 - rg.LEVEL) / 2 * 100)
    return Entry(
        name="projection_range",
        title=f"{rg.LEVEL:.0%} range",
        kind="metric",
        modules=("regression_watch",),
        unit="points per game",
        formula=(
            f"ppg_ros + the {tail}th and {100 - tail}th percentiles of the frozen backtest's "
            "per-game misses (rest_of_season_ppg - ppg_ros) at his position, from lists with "
            f"weeks left within {rg.HORIZON_WINDOW} of his list's (else the nearest weeks left), "
            "of seasons before the list's only (walk-forward); none with fewer than "
            f"{rg.MIN_MISSES} misses"
        ),
        explanation=(
            "Where his rest-of-season points per game should land 8 times in 10: the "
            "projection plus how far off it was for similar players in earlier seasons. In "
            "the backtest the real result fell inside about 80% of the time at every position "
            "(the Methodology page shows each share); it says nothing about games he misses."
        ),
        source="twm.modules.regression_watch.ranges",
        step="F4",
        model_output=True,
    )


# The streamer's reason sentences (step S2a, twm.modules.streamer.reasons): (yes / value, no).
# The games-so-far features have none: they are never reasons (they say how much data there
# is, not why a pick is good).
_STREAMER_REASONS: dict[str, tuple[str, str | None]] = {
    "kdst_points_per_game": ("Has scored {value:.1f} fantasy points per game this season", None),
    "kdst_ppg_rank": ("Ranks #{value:.0f} at {pos} in fantasy points per game this season", None),
    "kdst_preseason_rank": ("Was ranked #{value:.0f} at {pos} before the season", None),
    "kdst_points_last": ("Scored {value:.1f} fantasy points in the last game", None),
    "is_team_kicker": ("Is {team}'s kicker now: he kicked in its latest game", None),
    "k_fg_att_per_game": ("Tries {value:.1f} field goals per game", None),
    "k_pat_att_per_game": ("Kicks {value:.1f} extra points per game (his offense scores "
                           "touchdowns)", None),
    "k_fg_att_40_plus_per_game": ("Tries {value:.1f} field goals of 40+ yards per game (long "
                                  "kicks score more)", None),
    "k_fg_pct_0_39": ("Has made {value:.0%} of his field goals under 40 yards this season", None),
    "k_fg_pct_40_49": ("Has made {value:.0%} of his 40-49-yard field goals this season", None),
    "k_fg_pct_50_plus": ("Has made {value:.0%} of his 50+ yard field goals this season", None),
    "dst_sacks_per_game": ("Gets {value:.1f} sacks per game", None),
    "dst_takeaways_per_game": ("Forces {value:.1f} takeaways (interceptions and fumbles) per "
                               "game", None),
    "dst_tds_per_game": ("Scores {value:.2f} defense or return touchdowns per game", None),
    "dst_points_allowed_per_game": ("Allows {value:.1f} points per game", None),
    "team_points_per_game": ("{team} scores {value:.1f} points per game", None),
    "team_rz_trips_per_game": ("{team} reaches the red zone {value:.1f} times per game", None),
    "team_rz_stalls_per_game": ("{team}'s red-zone drives stall {value:.1f} times per game "
                                "(short field-goal chances)", None),
    "team_rz_stall_rate": ("{team} scores no touchdown on {value:.0%} of its red-zone trips",
                           None),
    "next_opp_points_allowed_per_game": ("Next week's opponent allows {value:.1f} points per "
                                         "game", None),
    "next_opp_rz_trips_allowed_per_game": ("Next week's opponent allows {value:.1f} red-zone "
                                           "trips per game", None),
    "next_opp_rz_stall_rate_forced": ("Next week's opponent keeps {value:.0%} of red-zone trips "
                                      "out of the end zone", None),
    "next_opp_points_per_game": ("Next week's opponent scores {value:.1f} points per game", None),
    "next_opp_sacks_allowed_per_game": ("Next week's opponent gives up {value:.1f} sacks per "
                                        "game", None),
    "next_opp_giveaways_per_game": ("Next week's opponent turns the ball over {value:.1f} times "
                                    "per game", None),
    "next_is_home": ("Plays at home next week", None),
    "next_venue_dome": ("Next week's game is indoors: no wind or rain", None),
    "next_venue_retractable": ("Next week's stadium has a retractable roof", None),
    "weekly_ecr_rank": ("Experts ranked {player} #{value:.0f} at {pos} last week", None),
    "weekly_ecr_listed": ("Was on the experts' weekly {pos} list", None),
}  # fmt: skip


def _streamer_entries() -> list[Entry]:
    """The K and D/ST streamer's features (step S1c, twm.modules.streamer.features) and its
    label (S1b), one entry per column."""
    from twm.modules.streamer.features import RED_ZONE_YARDLINE
    from twm.modules.streamer.pool import StreamerRules

    starts = StreamerRules.from_config().start_thresholds
    per = (
        "per game = mean over the team's regular-season games of the season visible at the "
        "as-of (NULL before its first game)"
    )
    k_per = (
        "over the kicker's regular-season games of the season with a field-goal or extra-point "
        "attempt visible at the as-of, for any team (NULL for DST rows and for a kicker "
        "without one)"
    )
    trip = (
        f"red-zone trip = a drive (fact_play.fixed_drive) with a snap at the opponent's "
        f"{RED_ZONE_YARDLINE} or closer (pass, run, field goal, kneel, spike or a penalty snap; "
        "two-point tries excluded); stall = a trip whose drive result is not 'Touchdown'"
    )
    nxt = (
        "the entity's as-of team's week N+1 opponent (fact_schedule; NULL on a bye or after "
        "the last regular-season week)"
    )
    feat = "twm.modules.streamer.features"
    rows: list[tuple[str, str, str, str, str, str]] = [
        # name, title, unit, formula, explanation, source
        ("kdst_points_per_game", "K/DST points per game", "points per game",
         "the pool's ppg_to_date: kicking: / defense: points (config/scoring.yaml) per "
         "regular-season game visible at the as-of (K: games with a kick attempt; DST: the "
         "team's games)",
         "How many fantasy points the kicker or defense has scored per game so far.",
         "fact_kicker_week, fact_defense_week (twm.scoring_kdst)"),
        ("kdst_ppg_rank", "K/DST points-per-game rank", "rank (1 = best)",
         "the pool's ppg_pos_rank: rank of kdst_points_per_game among the position's universe "
         "with a game (ties share the better rank)",
         "Where its points per game rank at its position so far.",
         "twm.modules.streamer.pool"),
        ("kdst_preseason_rank", "K/DST preseason rank", "rank (1 = best)",
         "the pool's preseason_pos_rank: 2020 on the rank on FantasyPros' last August/"
         "September K or DST cheat sheet before week 1; earlier the rank by last season's "
         "points per game",
         "Where experts (or last season's scoring) placed it before the season.",
         "fact_ranking_kdst.pos_rank; twm.modules.streamer.pool"),
        ("kdst_games_to_date", "K/DST games so far", "games",
         "the pool's games_to_date (K: games with a kick attempt; DST: team games)",
         "How many games the numbers so far are based on.", "twm.modules.streamer.pool"),
        ("kdst_points_last", "K/DST points, latest game", "points",
         "fantasy points in the entity's most recent visible regular-season game of the "
         "season (K: his latest game with a kick attempt, any team)",
         "What it scored last time out.", "fact_kicker_week, fact_defense_week"),
        ("is_team_kicker", "Team's kicker", "boolean",
         "the kicker has a kick attempt in his as-of team's latest visible regular-season "
         "game with one (the pool's is_team_kicker; NULL for DST rows)",
         "Whether he is the kicker his team is using now; practice-squad and camp kickers "
         "are in the pool but rarely kick.", "fact_kicker_week.team; twm.modules.streamer.pool"),
    ]  # fmt: skip
    acc = (
        "fg_made / (fg_made + fg_missed) in the distance bucket, summed {k_per}; blocked kicks "
        "are not in nflverse's distance buckets and are left out; NULL without an attempt there"
    ).replace("{k_per}", k_per)
    rows += [
        ("k_fg_att_per_game", "Field-goal attempts per game", "attempts per game",
         f"sum of fg_att / games, {k_per}",
         "How often his team sends him out for field goals: the main source of kicker points.",
         "fact_kicker_week.fg_att"),
        ("k_pat_att_per_game", "Extra-point attempts per game", "attempts per game",
         f"sum of pat_att / games, {k_per}",
         "Extra points follow touchdowns, so this shows how often his offense scores.",
         "fact_kicker_week.pat_att"),
        ("k_fg_att_40_plus_per_game", "Long field-goal attempts per game", "attempts per game",
         f"field goals made or missed from 40+ yards / games, {k_per} (blocked kicks have no "
         "distance bucket)",
         "Long kicks score more fantasy points than short ones in the default scoring.",
         "fact_kicker_week.fg_made_40_49, fact_kicker_week.fg_missed_40_49, "
         "fact_kicker_week.fg_made_50_59, fact_kicker_week.fg_made_60_"),
        ("k_fg_pct_0_39", "Field-goal accuracy, 0-39 yards", "share (0-1)",
         f"{acc} (0-19, 20-29 and 30-39 yards)",
         "How reliable he is on short kicks this season.",
         "fact_kicker_week.fg_made_30_39, fact_kicker_week.fg_missed_30_39"),
        ("k_fg_pct_40_49", "Field-goal accuracy, 40-49 yards", "share (0-1)",
         f"{acc} (40-49 yards)", "How reliable he is on medium-long kicks this season.",
         "fact_kicker_week.fg_made_40_49, fact_kicker_week.fg_missed_40_49"),
        ("k_fg_pct_50_plus", "Field-goal accuracy, 50+ yards", "share (0-1)",
         f"{acc} (50-59 and 60+ yards)",
         "How reliable he is from 50 yards and beyond; coaches trust a strong leg with more "
         "long tries.", "fact_kicker_week.fg_made_50_59, fact_kicker_week.fg_missed_50_59"),
        ("dst_sacks_per_game", "Sacks per game", "sacks per game",
         f"mean def_sacks (half sacks count 0.5), {per}; NULL for K rows",
         "How often the defense sacks the quarterback; every sack scores for a team defense.",
         "fact_defense_week.def_sacks"),
        ("dst_takeaways_per_game", "Takeaways per game", "takeaways per game",
         f"mean (def_interceptions + fumble_recovery_opp), {per}; NULL for K rows",
         "Interceptions and recovered fumbles: both score for a team defense.",
         "fact_defense_week.def_interceptions, fact_defense_week.fumble_recovery_opp"),
        ("dst_tds_per_game", "Defense and return touchdowns per game", "touchdowns per game",
         "mean (kickoff, punt, interception, fumble and blocked-kick return TDs), "
         f"{per}; NULL for K rows",
         "Touchdowns scored by the defense or the return teams: rare, but worth a lot.",
         "fact_defense_week.interception_return_tds, fact_defense_week.fumble_return_tds, "
         "fact_defense_week.kickoff_return_tds, fact_defense_week.punt_return_tds, "
         "fact_defense_week.blocked_kick_return_tds"),
        ("dst_points_allowed_per_game", "Points allowed per game (ESPN rule)", "points per game",
         "mean points_allowed (the opponent's score minus 6 for each touchdown the team's own "
         f"offense gave up on an interception or fumble return), {per}; NULL for K rows",
         "How many points the defense gives up; fewer points allowed earn more fantasy "
         "points.", "fact_defense_week.points_allowed"),
    ]  # fmt: skip
    rows += [
        ("team_points_per_game", "Team points per game", "points per game",
         f"mean of the entity's as-of team's final scores, {per}",
         "How much the team's offense scores: more scoring drives mean more kicks.",
         "fact_game.home_score, fact_game.away_score"),
        ("team_games_to_date", "Team games so far", "games",
         "the as-of team's regular-season games of the season with a final score visible at "
         "the as-of", "How many games the team numbers are based on.", "fact_game.result"),
        ("team_rz_trips_per_game", "Red-zone trips per game", "trips per game",
         f"the as-of team's red-zone trips / its games with play-by-play ({trip})",
         "How often the offense gets inside the opponent's 20-yard line.",
         "fact_play.yardline_100, fact_play.fixed_drive"),
        ("team_rz_stalls_per_game", "Red-zone stalls per game", "stalls per game",
         f"the as-of team's red-zone trips that did not end in a touchdown / its games with "
         f"play-by-play ({trip})",
         "Drives that reach the red zone but stall usually end in a short field goal: kicker "
         "points.", "fact_play.fixed_drive_result"),
        ("team_rz_stall_rate", "Red-zone stall rate", "share (0-1)",
         f"stalls / trips of the as-of team this season ({trip}); NULL without a trip",
         "The share of red-zone trips that end without a touchdown.",
         "fact_play.fixed_drive_result"),
        ("next_opp_points_allowed_per_game", "Next opponent: points allowed per game",
         "points per game", f"mean of the scores against {nxt}, {per}",
         "A defense that gives up many points means more scoring chances for this team.",
         "fact_game.home_score, fact_game.away_score"),
        ("next_opp_rz_trips_allowed_per_game", "Next opponent: red-zone trips allowed",
         "trips per game", f"red-zone trips of offenses facing {nxt} / its games with "
         f"play-by-play ({trip})",
         "How often offenses reach the red zone against next week's opponent.",
         "fact_play.yardline_100, fact_play.defteam"),
        ("next_opp_rz_stall_rate_forced", "Next opponent: red-zone stall rate forced",
         "share (0-1)", f"stalls / trips of offenses facing {nxt} ({trip}); NULL without a trip",
         "A defense that holds teams to field goals in the red zone helps kickers.",
         "fact_play.fixed_drive_result, fact_play.defteam"),
        ("next_opp_points_per_game", "Next opponent: points per game", "points per game",
         f"mean final score of {nxt}, {per}",
         "A weak offense next week is a good matchup for a team defense.",
         "fact_game.home_score, fact_game.away_score"),
        ("next_opp_sacks_allowed_per_game", "Next opponent: sacks allowed per game",
         "sacks per game", f"mean def_sacks of the defenses that faced {nxt}, {per}",
         "An offense that gets sacked a lot gives a defense sack points.",
         "fact_defense_week.def_sacks, fact_defense_week.opponent_team"),
        ("next_opp_giveaways_per_game", "Next opponent: giveaways per game",
         "turnovers per game",
         f"mean (def_interceptions + fumble_recovery_opp) of the defenses that faced {nxt}, "
         f"{per}", "An offense that throws interceptions and loses fumbles feeds a defense.",
         "fact_defense_week.def_interceptions, fact_defense_week.fumble_recovery_opp"),
        ("next_opp_games_to_date", "Next opponent: games so far", "games",
         f"regular-season games with a final score of {nxt}",
         "How many games the opponent numbers are based on.", "fact_game.result"),
        ("next_is_home", "Home game next week", "boolean",
         "the as-of team is the home team of its week N+1 game and the venue is not neutral "
         "(fact_schedule.location, public from slot_available_at); NULL on a bye or while the "
         "venue is not public", "Teams tend to score more and allow less at home.",
         "fact_schedule.home_team, fact_schedule.location"),
        ("next_venue_dome", "Next game under a fixed roof", "boolean",
         "the week N+1 stadium (fact_schedule.stadium_id, used once fact_schedule.stadium is "
         "public) has a roof value 'dome' in earlier games visible at the as-of and never "
         "open/closed; NULL without an earlier game there",
         "No wind or rain indoors, so kicks are easier.", "fact_game.roof, fact_game.stadium_id"),
        ("next_venue_retractable", "Next game under a retractable roof", "boolean",
         "an earlier visible game at the week N+1 stadium has roof 'open' or 'closed' (the "
         "game-day state itself is never used: it is decided on game day); NULL without an "
         "earlier game there", "A retractable roof is often closed in bad weather.",
         "fact_game.roof, fact_schedule.stadium_id"),
        ("weekly_ecr_rank", "Weekly expert rank", "rank (1 = best)",
         "the entity's pos_rank on FantasyPros' latest weekly K or DST ranking of the season "
         "visible at the as-of (the Friday before week N's games; late 2020 on); K by gsis_id, "
         "DST by team; NULL when not listed or no page", "Where experts ranked it last week.",
         "fact_ranking_kdst.pos_rank"),
        ("weekly_ecr_listed", "On the weekly expert list", "boolean",
         "a weekly K or DST page of the season is visible and lists the entity; NULL when no "
         "page is visible (before late 2020)",
         "Experts list only the kickers and defenses worth starting.",
         "fact_ranking_kdst.page_kind"),
    ]  # fmt: skip
    return [
        Entry(
            name=name,
            title=title,
            kind="feature",
            modules=("streamer",),
            unit=unit,
            formula=formula,
            explanation=explanation,
            source=f"{feat}; {source}",
            step="S1c",
            reason_template=_STREAMER_REASONS.get(name, (None, None))[0],
            reason_if_false=_STREAMER_REASONS.get(name, (None, None))[1],
        )
        for name, title, unit, formula, explanation, source in rows
    ] + [
        Entry(
            name="y_start",
            title="Streamer label: a starter next week",
            kind="label",
            modules=("streamer",),
            unit="boolean",
            formula="the entity's week N+1 fantasy points (config/scoring.yaml kicking: / "
            "defense:) rank in the top (teams x lineup slots) at its position among everyone "
            f"who played that week: top {starts['K']} K and top {starts['DST']} DST in this "
            "league (ties at the cutoff all count); NULL on a bye, after the last regular-"
            "season week, or while week N+1 is not final; a kicker without a kick in week N+1 "
            "is False",
            explanation="Would the kicker or defense you pick up on Tuesday have been worth "
            "starting in the very next game? Streaming is a one-week decision.",
            source="twm.modules.streamer.labels (fact_kicker_week, fact_defense_week)",
            step="S1b",
        ),
    ]


def _decisions_entries() -> list[Entry]:
    """G1: the features and label of the app's own win-probability model
    (twm.modules.decisions.wp_data, twm.modules.decisions.wp)."""
    play = "twm.modules.decisions.wp_data"
    rows = [
        ("score_differential", "Score difference (offense minus defense)", "points",
         "possession team's score minus the defense's, before the snap",
         "How far ahead (positive) or behind (negative) the team with the ball is.",
         "fact_play.score_differential"),
        ("game_seconds_remaining", "Seconds left in the game", "seconds",
         "seconds left in regulation (in overtime: in the overtime period), before the snap",
         "How much time is left. A 7-point lead means little early and a lot late.",
         "fact_play.game_seconds_remaining"),
        ("half_seconds_remaining", "Seconds left in the half", "seconds",
         "seconds left in the current half (or overtime period), before the snap",
         "Time left before halftime or the end of the game; drives the two-minute drill.",
         "fact_play.half_seconds_remaining"),
        ("down", "Down", "1-4", "the down before the snap",
         "Which of the offense's four tries to gain the distance this is.",
         "fact_play.down"),
        ("ydstogo", "Yards to go", "yards", "yards needed for a first down (or a touchdown)",
         "The distance the offense still needs; 3rd and 1 is far better than 3rd and 12.",
         "fact_play.ydstogo"),
        ("yardline_100", "Yards to the end zone", "yards (1-99)",
         "yards between the line of scrimmage and the opponent's goal line",
         "Field position: 1 = at the opponent's goal line, 99 = backed up at your own 1.",
         "fact_play.yardline_100"),
        ("posteam_timeouts_remaining", "Offense timeouts left", "0-3",
         "the possession team's timeouts left in the half, before the snap",
         "Timeouts stop the clock: a trailing team with timeouts has more time to come back.",
         "fact_play.posteam_timeouts_remaining"),
        ("defteam_timeouts_remaining", "Defense timeouts left", "0-3",
         "the defense's timeouts left in the half, before the snap",
         "The other team's timeouts: they can stop the clock to get the ball back.",
         "fact_play.defteam_timeouts_remaining"),
        ("half_number", "Half", "1, 2 or 3", "1 = first half, 2 = second half, 3 = overtime "
         "(from fact_play.game_half)",
         "Which half the play is in. Halftime resets timeouts and gives one team the ball.",
         "fact_play.game_half"),
        ("receives_2h_kickoff", "Gets the ball after halftime", "boolean (0/1)",
         "first half only: 1 when the possession team kicked the opening kickoff (it receives "
         "the second-half kickoff); 0 in the second half and overtime",
         "In the first half, knowing you get the ball back after halftime is worth a little.",
         "fact_play.kickoff_attempt, fact_play.defteam (the game's first kickoff)"),
    ]  # fmt: skip
    rows += [
        ("posteam_is_home", "Offense at home", "1 / 0 / 0.5",
         "1 when the possession team is the home team, 0 when away, 0.5 at a neutral site "
         "(fact_game.location)",
         "Home teams win a bit more often; neutral-site games (London, Super Bowl) have no "
         "home team.", "fact_game.location"),
        ("posteam_spread", "Pregame spread (offense's view)", "points",
         "closing spread_line if the possession team is home, else minus spread_line "
         "(spread_line > 0 = home favored): the margin the market expected for the offense",
         "How many points the betting market expected the team with the ball to win by, set "
         "right before kickoff (allowed: it is known when the game starts).",
         "fact_game.spread_line"),
        ("spread_time", "Spread x time left", "points",
         "posteam_spread x exp(-4 x elapsed share of regulation; overtime = 1)",
         "The pregame expectation fades as the game goes on; this lets the model weigh it "
         "less and less.", "fact_game.spread_line, fact_play.game_seconds_remaining"),
        ("diff_time_ratio", "Lead x time played", "points",
         "score_differential / exp(-4 x elapsed share of regulation; overtime = 1)",
         "The same lead is worth more late in the game; this number grows as time runs out.",
         "fact_play.score_differential, fact_play.game_seconds_remaining"),
        ("era_pat_2015", "Era: long extra point (2015+)", "boolean (0/1)",
         "1 for seasons 2015 and later (extra points snapped from the 15-yard line)",
         "Extra points got harder in 2015, which slightly changes what a touchdown is worth.",
         "fact_play.season"),
        ("era_kickoff_2023", "Era: new kickoff rules (2023+)", "boolean (0/1)",
         "1 for seasons 2023 and later (fair-catch touchbacks to the 25 in 2023, the dynamic "
         "kickoff from 2024)",
         "Kickoff rules changed where drives start after a score.", "fact_play.season"),
    ]  # fmt: skip
    smooth = [
        ("drive_value", "Value of the ball before the half ends", "points",
         "half_value(yardline_100, half_seconds_remaining): the offense's points minus the "
         "defense's from a 1st down at this spot and clock until halftime, a fixed table "
         "measured on the 1999-2005 first halves (wp_data.HALF_VALUE_TABLE), read "
         "linearly in yards and log seconds; 0 when the half is over",
         "What having the ball here is worth before halftime (or the end), the other team's "
         "later possessions included: about 4 points at the opponent's 20 early in a half, "
         "about 2 at midfield, 0 when the half ends.",
         "fact_play.yardline_100, fact_play.half_seconds_remaining"),
        ("z_margin", "Lead in standard deviations of what is left", "z-score",
         "(score_differential + drive_value + posteam_spread x t) / (13.5 x sqrt(t) + 1), "
         "t = share of regulation left",
         "A random-walk view of the game: how many 'typical swings of the remaining time' the "
         "offense is ahead by, counting the ball and the spread.",
         "fact_play.score_differential, fact_game.spread_line, "
         "fact_play.game_seconds_remaining"),
    ]  # fmt: skip
    return [
        Entry(name=name, title=title, kind="feature", modules=("decisions",), unit=unit,
              formula=formula, explanation=explanation, source=f"{source}; {play}", step="G1")
        for name, title, unit, formula, explanation, source in rows
    ] + [
        Entry(name=name, title=title, kind="feature", modules=("decisions",), unit=unit,
              formula=formula, explanation=explanation, source=f"{source}; {play}",
              step="G1b")
        for name, title, unit, formula, explanation, source in smooth
    ] + [
        Entry(
            name="posteam_wins",
            title="WP label: the team with the ball won",
            kind="label",
            modules=("decisions",),
            unit="boolean",
            formula="1 when the possession team before the snap won the game (fact_game.result "
            "from its side > 0), 0 when it lost; tie games are left out",
            explanation="What the win-probability model learns to predict, play by play.",
            source="fact_game.result; twm.modules.decisions.wp_data",
            step="G1",
        ),
        Entry(
            name="own_wp",
            title="Win probability (our model)",
            kind="metric",
            modules=("decisions",),
            unit="probability (0-1)",
            formula="trained walk-forward on seasons before the play's season (G1); since G1b a "
            "smooth possession-symmetric spline logistic model of the play state up to 15:00 of "
            "the fourth quarter, handing over to a monotone LightGBM by 10:00 left "
            "(wp_select.CHOSEN); isotonic calibration only where it helped on the validation "
            "season and kept the model within the smoothness limits",
            explanation="The chance the offense wins from this moment, from a model that never "
            "saw the season it scores. It replaces nflfastR's wp in the decision grades.",
            source="twm.modules.decisions.wp",
            step="G1",
        ),
    ]  # fmt: skip


def _submodel_entries() -> list[Entry]:
    """G2: the features, labels and outputs of the fourth-down sub-models
    (twm.modules.decisions.conversion, fieldgoal, punt, tries)."""
    conv, fg = "twm.modules.decisions.conversion", "twm.modules.decisions.fieldgoal"
    rows = [
        ("goal_to_go", "Goal to go", "boolean (0/1)",
         "1 when the line to gain is the goal line (fact_play.goal_to_go; for a hypothetical "
         "state: ydstogo >= yardline_100)",
         "Near the goal line a first down is a touchdown, and the defense has less field to "
         "cover.", f"fact_play.goal_to_go; {conv}"),
        ("is_fourth_down", "Fourth down", "boolean (0/1)", "1 when down = 4, 0 on third down",
         "Separates fourth-down tries (a chosen gamble) from third downs (which add sample).",
         f"fact_play.down; {conv}"),
        ("fg_distance", "Field-goal distance", "yards",
         "yardline_100 + 18 (10 yards of end zone + the hold about 8 yards behind the line; "
         "fact_play.kick_distance - yardline_100 is 18 on 88% of 2023-2025 kicks, 19 on 12%)",
         "How long the kick would be, known before the snap.", f"fact_play.yardline_100; {fg}"),
        ("roof_closed", "Indoors", "boolean (0/1)",
         "1 when fact_game.roof is dome or closed; 0 for outdoors, open or unknown",
         "No wind or cold indoors.", f"fact_game.roof; {fg}"),
        ("temp_f", "Temperature", "degrees F",
         "fact_game.temp, else the 'Temp: N' in fact_play.weather; indoors = 70; still unknown "
         "outdoors -> NULL, filled with the median of the training rows' outdoor games",
         "Cold air and a hard ball make long kicks shorter.",
         f"fact_game.temp, fact_play.weather; {fg}"),
        ("wind_mph", "Wind", "mph",
         "fact_game.wind, else the 'Wind: ... N mph' in fact_play.weather ('calm' = 0); "
         "indoors = 0; still unknown outdoors -> NULL, filled with the training median",
         "Wind pushes long kicks off line.", f"fact_game.wind, fact_play.weather; {fg}"),
        ("surface_grass", "Grass field", "boolean (0/1)",
         "1 for grass or dessograss, 0 for artificial turf; unknown -> NULL, filled with the "
         "training median", "Kicking footing differs a little between grass and turf.",
         f"fact_game.surface; {fg}"),
        ("weather_missing", "Weather unknown", "boolean (0/1)",
         "1 for an outdoor game whose temperature or wind is unknown even after the weather "
         "text (then imputed)", "Flags the games whose weather was filled in, so the model "
         "can treat them apart.", f"fact_game.temp/wind, fact_play.weather; {fg}"),
    ]  # fmt: skip
    out = [
        Entry(name=name, title=title, kind="feature", modules=("decisions",), unit=unit,
              formula=formula, explanation=explanation, source=source, step="G2")
        for name, title, unit, formula, explanation, source in rows
    ]  # fmt: skip
    labels = [
        ("converted", "Go label: the try converted", "label", "boolean",
         "third or fourth down pass/run: first_down = 1 or the offense's touchdown, and no "
         "interception or lost fumble (defensive penalties that give a first down count)",
         "Whether going for it worked: the offense kept the ball with a new set of downs.", conv),
        ("fg_made", "FG label: the kick was good", "label", "boolean",
         "fact_play.field_goal_result = 'made' (missed and blocked = 0)",
         "Whether the field goal went through.", fg),
        ("p_convert", "Conversion probability", "metric", "probability (0-1)",
         "LightGBM on the go-for-it features, trained walk-forward (seasons < S)",
         "The chance that going for it on this down and distance works.", conv),
        ("p_fg_make", "Field-goal probability", "metric", "probability (0-1)",
         "LightGBM on distance, roof, wind, temperature, surface and era, walk-forward",
         "The chance the kick is good from here, in these conditions.", fg),
        ("punt_expected_wp", "Expected WP after a punt", "metric", "probability (0-1)",
         "sum over the smoothed empirical distribution of punt results (receiving team's "
         "spot, kicking team recovers, return touchdown) of the kicking team's own WP (G1) "
         "in the resulting state", "How good punting is, as a win chance, averaged over "
         "where punts from this spot really end up.", "twm.modules.decisions.punt"),
        ("pat_rate", "Extra-point rate", "metric", "share (0-1)",
         "made extra points / attempts in the last 5 seasons before S within the same rule "
         "era (2015+: from the 15)", "How often the kick after a touchdown is good.",
         "twm.modules.decisions.tries"),
        ("two_point_rate", "Two-point rate", "metric", "share (0-1)",
         "successful two-point tries / tries in the last 5 seasons before S",
         "How often a two-point try succeeds.", "twm.modules.decisions.tries"),
    ]  # fmt: skip
    return out + [
        Entry(name=n, title=t, kind=k, modules=("decisions",), unit=u, formula=f,
              explanation=e, source=src, step="G2")
        for n, t, k, u, f, e, src in labels
    ]  # fmt: skip


def _grade_entries() -> list[Entry]:
    """G3: the fourth-down and two-point grades and the coach aggregates
    (twm.modules.decisions.grade, grade_inputs, coach; rules in docs/decision_metrics.md)."""
    g, gi, c = ("twm.modules.decisions.grade", "twm.modules.decisions.grade_inputs",
                "twm.modules.decisions.coach")  # fmt: skip
    rows = [
        ("wp_go", "WP if going for it", "probability (0-1)",
         "P(convert) x expected WP after a conversion + (1 - P(convert)) x expected WP after "
         "a failure (G2 ball-spot tables, G1 WP; fold models of seasons < S)",
         "The offense's chance to win if it goes for it here.", g),
        ("wp_fg", "WP if kicking a field goal", "probability (0-1)",
         "P(make) x WP(3 points up, opponent receives the kickoff) + (1 - P(make)) x WP(the "
         "opponent's ball at the spot of the kick or its 20); only within the longest field "
         "goal made before S", "The offense's chance to win if it kicks.", g),
        ("wp_punt", "WP if punting", "probability (0-1)",
         "G2's punt_expected_wp with the measured kickoff spot after a return touchdown; only "
         "from yardlines punts come from", "The offense's chance to win if it punts.", g),
        ("wp_kick", "WP if kicking the extra point", "probability (0-1)",
         "PAT rate x WP(+1) + (1 - PAT rate) x WP(+0), the opponent then receiving the kickoff",
         "The scoring team's chance to win if it kicks after a touchdown.", g),
        ("wp_two_point", "WP if going for two", "probability (0-1)",
         "two-point rate x WP(+2) + (1 - rate) x WP(+0), the opponent then receiving",
         "The scoring team's chance to win if it goes for two.", g),
        ("wp_lost", "WP lost", "probability (0-1; shown as WP points)",
         "WP of the best option - WP of the chosen option (0 when the best was chosen); "
         "summed over clear decisions only",
         "How much win probability a decision gave away by the model's numbers.", g),
        ("decision_grade", "Clear call or toss-up", "category",
         "'clear' when the best option's WP beats the second best by more than "
         "decisions.toss_up_margin (1.5 WP points), else 'toss_up' (not graded); the 4th "
         "quarter's last 2:00 and overtime are not graded at all (decisions.late_game)",
         "Only decisions with a clear answer count against a coach.", g),
        ("kickoff_spot", "Kickoff spot after a score", "yardline_100",
         "mean receiving start of the same season's kickoffs in earlier weeks (at least "
         "decisions.kickoff_min_kicks), else the previous season's, rounded to the yard",
         "Where the other team starts after a score (the kickoff rules changed in 2024 and "
         "2025, so it is measured, not assumed).", gi),
        ("aggressiveness", "Aggressiveness", "share (0-1)",
         "clear fourth downs where going for it was best AND the coach went / clear fourth "
         "downs where going for it was best (the go rate when go was clearly best)",
         "How often a coach goes for it when the numbers clearly say go.", c),
        ("wp_lost_per_game", "WP lost per game", "probability (0-1; shown as WP points)",
         "(fourth-down + two-point WP lost on clear decisions) / games coached",
         "A coach's decision cost per game, for the leaderboard.", c),
    ]  # fmt: skip
    return [
        Entry(name=n, title=t, kind="metric", modules=("decisions",), unit=u, formula=f,
              explanation=e, source=src, step="G3")
        for n, t, u, f, e, src in rows
    ]  # fmt: skip


def _hot_seat_entries() -> list[Entry]:
    """H3a: the Hot-Seat features (twm.modules.hot_seat.features; docs/hot_seat.md). One row per
    head coach x team x as-of; "to date" = the team's regular-season games of the season whose
    rows are public at the as-of (Tuesday 14:00 UTC after week N, or the end-of-season one)."""
    hs = "twm.modules.hot_seat.features"
    feats = [
        ("reg_games_played", "Games played to date", "games",
         "the team's played regular-season games of the season visible at the as-of (fact_game)",
         "How many games the team has played so far this season."),
        ("reg_wins", "Wins to date", "wins", "regular-season wins to date, a tie = 0.5",
         "The team's wins so far, ties counting as half a win."),
        ("expected_wins", "Market-expected wins to date", "wins",
         "sum over the games to date of the team's pregame win probability: de-vigged closing "
         "moneylines p = (1/o_team) / (1/o_team + 1/o_opp) on decimal odds (American +150 -> "
         "2.5, -200 -> 1.5); a game without both moneylines uses 1 / (1 + exp(-k x spread_line)) "
         "(home side; k fit by maximum likelihood on every played game of the seasons before, "
         "ties out); NULL if a game has neither",
         "How many games the betting market expected the team to have won by now."),
        ("wins_vs_expected", "Wins vs market expectation", "wins",
         "reg_wins - expected_wins",
         "Positive: the team has won more than the market expected; negative: fewer."),
        ("point_diff_per_game", "Point differential per game", "points per game",
         "(points scored - points allowed) / games, regular season to date",
         "By how much the team outscores (or is outscored by) its opponents on average."),
        ("pythagorean_wins", "Pythagorean wins", "wins",
         "games x PF^e / (PF^e + PA^e), e = hot_seat.pythagorean_exponent (2.37, the NFL "
         "exponent Football Outsiders uses); NULL when PF + PA = 0",
         "The wins a team 'deserves' from its points scored and allowed."),
        ("pythag_minus_wins", "Pythagorean wins minus wins", "wins",
         "pythagorean_wins - reg_wins",
         "Positive: the team has been unlucky in close games; negative: lucky."),
        ("off_epa_neutral", "Offensive EPA per play (neutral)", "points per play",
         "sum(epa) / plays over the team's run and pass plays (no two-point tries) in neutral "
         "situations (fact_play.is_neutral), games to date; plays weighted equally",
         "How efficient the offense is when the score does not force its hand."),
        ("def_epa_neutral", "Defensive EPA per play allowed (neutral)", "points per play",
         "as off_epa_neutral for the opponents' plays against the team (defteam); higher = worse",
         "How efficient opponents are against this defense; lower is better."),
        ("off_epa_neutral_trend", "Offensive EPA trend", "points per play",
         "off_epa_neutral over the team's last hot_seat.trend_games (4) games minus the season "
         "to date; NULL until the team has played more than 4 games",
         "Positive: the offense has been better lately than over the whole season."),
        ("def_epa_neutral_trend", "Defensive EPA trend", "points per play",
         "def_epa_neutral over the last 4 games minus the season to date (NULL until more than 4 "
         "games); positive = the defense has been worse lately",
         "Positive: the defense has been allowing more lately than over the whole season."),
    ]  # fmt: skip
    feats += [
        ("tenure_seasons", "Coach tenure", "seasons",
         "seasons of the coach's current stint with the team, this one included: consecutive "
         "seasons back from this one in which he coached it a game (coach_game, kickoff <= as-of)",
         "How long the coach has been in charge of this team."),
        ("is_first_year_coach", "First-year coach", "boolean", "tenure_seasons = 1",
         "The coach is in his first season with this team."),
        ("is_second_year_coach", "Second-year coach", "boolean", "tenure_seasons = 2",
         "The coach is in his second season with this team."),
        ("tenure_censored", "Tenure starts before the data", "boolean",
         "the current stint reaches the warehouse's first season (1999), so the true tenure "
         "may be longer than tenure_seasons",
         "The coach was already in charge when our data begins; his tenure is a minimum."),
        ("prev_season_wins", "Previous season wins", "wins",
         "the team's regular-season wins last season (ties 0.5), whoever coached; NULL for a "
         "team without games last season",
         "How the team did last year."),
        ("prev_playoff_round", "Previous season playoff result", "round (0-5)",
         "last season: 0 no playoffs, 1 lost wild card, 2 lost divisional, 3 lost conference, "
         "4 lost Super Bowl, 5 won it (text in prev_playoff_result)",
         "How far the team went in last season's playoffs."),
        ("consecutive_losing_seasons", "Consecutive losing seasons", "seasons",
         "seasons right before this one, counted back until one is not losing or not coached by "
         "him, in which the coach coached the team and its regular-season win share (ties 0.5) "
         "was < 0.5",
         "How many losing seasons in a row the coach has had with this team before this one."),
        ("division_rank", "Division rank", "rank (1 = best)",
         "rank in the division (dim_team.team_division) by win share (wins + 0.5 ties) / games "
         "to date among the division's teams with a game; tied teams share the better rank",
         "Where the team stands in its division right now."),
        ("games_remaining", "Games remaining", "games",
         "season length (the most regular-season games any team has in the visible schedule) - "
         "games played - a listed cancelled game once gone (2022 BUF/CIN after week 17)",
         "How many regular-season games the team still has to play."),
        ("starting_qb_changes", "Starting-QB changes", "changes",
         "distinct starting QBs (fact_game home/away_qb_id) in the games to date minus 1",
         "How many different quarterbacks beyond the first have started this season."),
        ("rookie_r1_qb_on_roster", "Rookie first-round QB", "boolean",
         "a QB (fact_roster_week.position) on the team's latest visible weekly roster of the "
         "season (status not CUT/RET/UFA/TRD) was a first-round pick of this year's draft "
         "(dim_player draft_round = 1, draft_year = season); NULL without a visible roster",
         "The team drafted its quarterback of the future: owners tend to be patient."),
        ("took_over_mid_season", "Took over mid-season", "boolean",
         "the team's first played regular-season game this season had another coach",
         "The coach replaced someone during this season (usually an interim coach)."),
        ("fourth_down_wp_lost_per_game", "Fourth-down WP lost per game", "probability (0-1)",
         "sum of wp_lost on the team's clear (graded) fourth downs in its games to date / games "
         "to date, from the stored Decision Report Card grades (never regraded); NULL before "
         "2006 or when one of the games has no grades",
         "How much win probability the coach's fourth-down calls have cost per game."),
    ]  # fmt: skip
    out = [Entry(name=n, title=t, kind="feature", modules=("hot_seat",), unit=u, formula=f,
                 explanation=e, source=hs, step="H3a") for n, t, u, f, e in feats]  # fmt: skip
    out += [
        Entry(name="is_interim", title="Interim coach (row flag)", kind="concept",
              modules=("hot_seat",), unit="boolean",
              formula="took_over_mid_season OR the owner's data/manual/coach_departures.csv says "
              "the coach-team-season was interim (departure_type interim_not_retained or "
              "interim_suspected true)",
              explanation="Interim coaches are left out of training (spec 8.5). Uses the owner's "
              "hindsight file, so it selects rows and is never a model feature.",
              source=hs, step="H3a"),
        Entry(name="prev_playoff_result", title="Previous playoff result (text)", kind="concept",
              modules=("hot_seat",), unit="text",
              formula="prev_playoff_round as text: none, lost_wc, lost_div, lost_conf, lost_sb, "
              "won_sb",
              explanation="Last season's playoff exit, spelled out.", source=hs, step="H3a"),
    ]  # fmt: skip
    return out


def _clock_entries() -> list[Entry]:
    """G4: the clock-management metrics (twm.modules.decisions.clock; definitions in
    docs/decision_metrics.md, "Clock management"; thresholds in settings decisions.clock)."""
    ck = "twm.modules.decisions.clock"
    rows = [
        ("kneel_out_seconds", "Kneel-out seconds K(d, t)", "concept", "seconds",
         "n x p + max(0, n - 1 - t) x (g - p), n = 5 - down kneels, t = the defense's timeouts; "
         "p (kneel_play) and g (kneel_cycle) = median seconds from a kneel to the next snap with "
         "/ without a defensive timeout between, measured on the 5 seasons before S",
         "The most clock an offense can burn by kneeling from this down; if it is at least the "
         "time left, the game is over unless the defense's timeouts cut it."),
        ("timeouts_unused", "Timeouts unused in a lost one-score game", "metric",
         "timeouts (0-3) per team-game",
         "regulation loss by 1-8; the opponent's drive held the game's last snap and had a "
         "4th-quarter snap with <= 120 s where K(d, t) < clock <= K(d, 0) (t >= 1 the team's "
         "timeouts); the team's timeouts at that drive's last snap; a case when >= 1",
         "The team lost a close game while the opponent ran out the clock, with timeouts still "
         "in its pocket. A fact, not always a mistake: see timeout_seconds_wasted."),
        ("half_passivity", "End-of-half passivity", "metric", "case (boolean) per first half",
         "a first-half drive that ended the half with a tail of kneels / designed runs (no team "
         "timeout, no pass) whose first 1st down had >= 40 s and >= 1 timeout; a case when "
         "half_value there >= decisions.clock.passivity_min_ep (1.0 point)",
         "Running out the first half with time, timeouts and field position good enough that "
         "attacking was clearly worth points."),
        ("passivity_ep_left", "EP left on the table (end of half)", "metric", "points",
         "G1b half_value(yardline_100, half_seconds_remaining) at the decision snap minus 0 "
         "(kneeling scores nothing): net points from a first-half 1st down to halftime, "
         "measured on 1999-2005",
         "How many points, net of the risk, a typical team got from that spot and clock."),
        ("passivity_wp_left", "WP left on the table (end of half)", "metric",
         "probability (0-1; shown as WP points)",
         "WP(decision snap) - WP(the second half's first snap: same score, 3 timeouts each, the "
         "receiving team at the kickoff spot), both from season S's own WP fold model",
         "What running out the half cost in win probability, by the model."),
        ("timeout_seconds_wasted", "Seconds wasted with timeouts in hand", "metric",
         "seconds per team-game",
         "while down 1-8 in the final 2:00, after an opponent play that leaves K(d', t - 1) < "
         "clock <= K(d', -1) (decisive), no team timeout and a runoff (next snap's gap minus the "
         "play's measured seconds) >= 10 s; per opponent drive the first k such runoffs, k = "
         "the timeouts the team still held at the drive's end",
         "Clock the team let run when a timeout would have kept its last possession alive, "
         "while it kept timeouts it never used."),
    ]  # fmt: skip
    return [
        Entry(name=n, title=t, kind=k, modules=("decisions",), unit=u, formula=f,
              explanation=e, source=ck, step="G4")
        for n, t, k, u, f, e in rows
    ]  # fmt: skip


def _board_entries() -> list[Entry]:
    """I1b: the Cliff & Breakout Board features (twm.modules.board.features; docs/board.md). One
    row per QB/RB/WR/TE with a regular-season stat line in season S, read at the end-of-season
    snapshot of S; "games" = regular-season games with a stat line; "in his games" = summed over
    the team-games he played."""
    bf = "twm.modules.board.features"
    feats = [
        ("pos_rb", "Running back", "boolean", "his point-in-time position in S is RB",
         "Position flag (QB is the case with every flag off). A category, not an identifier."),
        ("pos_wr", "Wide receiver", "boolean", "his point-in-time position in S is WR",
         "Position flag."),
        ("pos_te", "Tight end", "boolean", "his point-in-time position in S is TE",
         "Position flag."),
        ("age", "Age after the season", "years",
         "(February 1 after season S - dim_player.birth_date) / 365.25; NULL without a birth date",
         "How old he is when the season ends; production falls with age, at different ages by "
         "position."),
        ("age_curve_ratio", "Aging curve", "ratio",
         "the position's quadratic fit of PPG(s+1) / PPG(s) on age over earlier season pairs "
         "(s + 1 <= S; top-48 in s, 6+ games in s+1; 50+ pairs), evaluated at his age (clipped "
         "to the fitted ages)",
         "What a typical player of his position and age keeps of his points per game next "
         "season, learned only from earlier seasons."),
        ("prior_seasons", "Prior seasons", "seasons",
         "S - fact_roster_week.entry_year (= years_exp): his seasons in the league before S",
         "Experience; the Cliff needs 3+, the Breakout 0 or 1."),
        ("games_s", "Games played", "games", "regular-season games with a stat line in S",
         "How many games he played (with a recorded play)."),
        ("ppg_s", "Points per game", "points per game",
         "regular-season fantasy points in S (config/scoring.yaml) / games_s",
         "His scoring rate last season."),
        ("pos_rank_s", "PPG rank at his position", "rank",
         "rank by ppg_s among his position's players with 8+ games in S (ties: more points, "
         "then id); NULL under 8 games",
         "Where he finished; also the prior-season-rank baseline's only input."),
        ("ppg_change", "PPG change", "points per game", "ppg_s - PPG in S-1 (NULL without S-1)",
         "A big jump is likely to give some back."),
        ("touches_s", "Touches", "touches", "carries + receptions in S",
         "Workload; very heavy loads (350+ for a running back) often precede a decline."),
        ("touches_per_game_s", "Touches per game", "touches per game", "touches_s / games_s",
         "Workload per game."),
        ("career_touches", "Career touches", "touches",
         "carries + receptions in every regular season 1999 .. S (a career before 1999 is cut)",
         "Mileage: wear accumulated over a career."),
        ("career_targets", "Career targets", "targets", "targets 1999 .. S",
         "Mileage as a receiver."),
        ("yards_per_touch_s", "Yards per touch", "yards",
         "(rushing + receiving yards) / touches in S; NULL under 20 touches",
         "Efficiency with the ball."),
        ("yards_per_touch_trend", "Yards-per-touch trend", "yards",
         "yards_per_touch_s - the mean of the S-1 and S-2 values that exist",
         "Negative: he is getting less out of each touch than before."),
    ]  # fmt: skip
    feats += [
        ("snap_pct_s", "Snap share", "share (0-1)",
         "mean fact_snaps.offense_pct over his regular-season games with an offensive snap in S "
         "(2013 on; NULL before)", "How much of the time he is on the field."),
        ("snap_pct_trend", "Snap-share trend", "share (0-1)", "snap_pct_s - the S-1 value",
         "Negative: his role is shrinking."),
        ("ngs_separation_s", "Separation (Next Gen Stats)", "yards",
         "target-weighted mean of avg_separation over his weekly NGS receiving rows of S (games "
         "with 5+ targets; 2016 on)",
         "How open he gets at the catch point; falls as receivers lose speed."),
        ("ngs_separation_trend", "Separation trend", "yards", "ngs_separation_s - the S-1 value",
         "Negative: he is getting less open than a year before."),
        ("ngs_ryoe_per_att_s", "Rush yards over expected per carry", "yards",
         "sum(rush_yards_over_expected) / sum(rush_attempts) over his weekly NGS rushing rows "
         "of S (games with 10+ carries; 2018 on: upstream has no RYOE for 2016-2017)",
         "Yards gained beyond what the blocking and defenders' positions predicted."),
        ("ngs_ryoe_trend", "RYOE trend", "yards", "ngs_ryoe_per_att_s - the S-1 value",
         "Negative: his running is losing its edge."),
        ("xfp_per_game_s", "Expected points per game (own xFP)", "points per game",
         "sum of his own walk-forward xFP (Regression Watch, each season from models trained on "
         "earlier seasons) over the regular season of S / games_s; NULL before 2009",
         "The points his opportunities were worth to an average player."),
        ("fpoe_per_game_s", "Points over expected per game", "points per game",
         "ppg_s - xfp_per_game_s", "Scoring beyond his opportunity, which tends not to last."),
        ("hc_departure", "Head-coach departure", "boolean",
         "his S team (his last regular-season game's) has a departure with last_season S in "
         "data/manual/coach_departures.csv announced before the snapshot's date (a blank date "
         "counts for fired/mutual/interim types only)",
         "A new head coach usually means a new offense and new roles."),
        ("target_share_s", "Target share", "share (0-1)",
         "his targets / his team's targets in his games of S", "His share of the passing game."),
        ("target_share_rookie", "Rookie target share", "share (0-1)",
         "target share in his rookie season (entry year; S itself for a first-year player)",
         "An early role is the strongest sign of a coming breakout."),
        ("yards_per_team_pass_att_s", "Yards per team pass attempt", "yards",
         "his receiving yards / his team's pass attempts in his games of S",
         "Production per team dropback: rewards both role and efficiency."),
        ("air_yards_share_s", "Air-yards share", "share (0-1)",
         "his receiving air yards / his team's in his games of S", "His share of the deep game."),
        ("rush_share_s", "Rush share", "share (0-1)",
         "his carries / his team's carries in his games of S", "His share of the running game."),
    ]  # fmt: skip
    feats += [
        ("drafted_round", "Draft round", "round (1-7)",
         "dim_player.draft_round; NULL when undrafted", "Teams give early picks more chances."),
        ("drafted_pick", "Draft pick", "overall pick", "dim_player.draft_pick; NULL undrafted",
         "Draft capital, finer than the round."),
        ("undrafted", "Undrafted", "boolean", "no draft round in dim_player", "Not drafted."),
        ("age_at_draft", "Age at the draft", "years",
         "(first day of his draft - birth_date) / 365.25; his entry year's draft when undrafted "
         "(available.DRAFT_FIRST_DAY, 2000-2026)",
         "Young draftees break out more often: they were productive in college earlier."),
        ("combine_forty", "40-yard dash", "seconds", "fact_combine.forty (his latest row)",
         "Straight-line speed."),
        ("combine_weight", "Combine weight", "pounds", "fact_combine.wt", "Size."),
        ("combine_height", "Combine height", "inches", "fact_combine.height_in", "Size."),
        ("combine_vertical", "Vertical jump", "inches", "fact_combine.vertical", "Explosiveness."),
        ("combine_broad_jump", "Broad jump", "inches", "fact_combine.broad_jump",
         "Explosiveness."),
        ("combine_speed_score", "Speed score", "index (100 = average RB)",
         "weight x 200 / forty^4 (Bill Barnwell's Speed Score, Football Outsiders 2008)",
         "Speed adjusted for size."),
        ("team_any_a", "Team ANY/A (QB quality)", "yards per attempt",
         "his S team's (passing yards + 20 x TD - 45 x INT - sack yards) / (attempts + sacks) "
         "in the regular season of S (Pro Football Reference's ANY/A; no model column)",
         "How good his team's passing game was."),
        ("third_season", "Entering his third season", "boolean", "S - entry year = 1",
         "Second-year (rookie season just ended) vs third-year player."),
    ]  # fmt: skip
    out = [Entry(name=n, title=t, kind="feature", modules=("board",), unit=u, formula=f,
                 explanation=e, source=bf, step="I1b") for n, t, u, f, e in feats]  # fmt: skip
    pre = [
        ("dc_absent", "Not on a week-1 depth chart", "boolean",
         "no row of his on any team's week-1 depth chart of S+1 visible at the preseason as-of "
         "(fact_depth_chart) while his S team's chart is visible; NULL when it is not "
         "(dc_team_chart_missing); the other preseason features are then NULL",
         "Cut, unsigned, retired or hurt before the season starts."),
        ("dc_team_chart_missing", "No depth chart for his team yet", "boolean",
         "no row of his on any visible week-1 chart of S+1 AND no visible chart of his S team "
         "at the preseason as-of (legacy charts are public the Wednesday before week 1; a "
         "team whose week-1 game moved has none)",
         "We cannot tell yet whether he made the team: a missing chart, not a cut."),
        ("team_change_s1", "New team", "boolean",
         "his week-1 chart team (the team where he has his best offense rank) differs from his S "
         "team (his last regular-season game's)", "He moved in the offseason."),
        ("depth_rank_s1", "Week-1 depth rank", "rank (1 = starter)",
         "his best depth_rank among his offense slots of his position (RB: RB/HB) on that chart; "
         "a daily pull's rank is renumbered within its slot", "Starter or backup going in."),
        ("new_competitor_s1", "New competition", "boolean",
         "a same-position teammate at his depth rank or ahead on that chart was on none of that "
         "team's regular-season weekly rosters of S", "A rookie or an arrival shares his role."),
        ("qb1_change_s1", "New starting QB", "boolean",
         "the team's S primary starter (most regular-season pass attempts for it in S) is not "
         "among its best-ranked QBs on that chart", "A new quarterback changes the targets."),
        ("vacated_targets_share_s1", "Vacated targets", "share (0-1)",
         "the team's S regular-season targets by players absent from its week-1 chart / the "
         "team's S targets", "Targets left behind by departed teammates."),
        ("vacated_carries_share_s1", "Vacated carries", "share (0-1)",
         "the same with carries", "Carries left behind by departed teammates."),
        ("hc_change_s1", "New head coach (week-1 team)", "boolean",
         "his week-1 chart team has a departure with last_season S in coach_departures.csv "
         "announced before the as-of's date (blank dates: the hc_departure rule)",
         "A new head coach on the team he will play for."),
    ]  # fmt: skip
    out += [Entry(name=n, title=t, kind="feature", modules=("board",), unit=u, formula=f,
                  explanation=e, source="twm.modules.board.preseason", step="I2a")
            for n, t, u, f, e in pre]  # fmt: skip
    pdr = [
        ("draft_pos_count_pd", "Draft picks at his position", "count",
         "picks of his S team in the S+1 draft (dim_player draft fields, public May 15) whose "
         "combine position of that year is his (RB: RB/HB); NULL when no pick is visible",
         "His team drafted competition."),
        ("draft_pos_best_round_pd", "Best round drafted at his position", "round (1 = first)",
         "the lowest draft_round among those picks; NULL when there is none",
         "How much capital the competition cost."),
        ("draft_pos_best_pick_pd", "Best pick drafted at his position", "overall pick",
         "the lowest overall draft_pick among those picks; NULL when there is none",
         "How much capital the competition cost."),
        ("draft_qb_r1_pd", "Rookie first-round QB", "boolean",
         "his S team drafted a combine-listed QB in round 1 of the S+1 draft",
         "A new quarterback may start."),
        ("draft_unplaced_pd", "Unplaced early pick", "boolean",
         "his S team made a round 1-3 pick of that draft with no combine row (position unknown "
         "point-in-time)", "The draft counts may miss a pick at his position."),
    ]  # fmt: skip
    out += [Entry(name=n, title=t, kind="feature", modules=("board",), unit=u, formula=f,
                  explanation=e, source="twm.modules.board.post_draft", step="I2b")
            for n, t, u, f, e in pdr]  # fmt: skip
    labels = [
        ("y_cliff", "Cliff", "PPG in S+1 <= 70% of PPG in S with 6+ games in S+1 (Cliff "
         "population: 3+ prior seasons, top-36 PPG at his position with 8+ games in S); NULL "
         "when y_missed", "A veteran whose scoring falls by 30% or more."),
        ("y_missed", "Missed next season", "fewer than 6 games in S+1 (Cliff population)",
         "Injury, benching, release or retirement: kept apart from the Cliff (owner, 2026-10-02)."),
        ("y_cliff_or_missed", "Cliff or missed", "y_cliff OR y_missed",
         "The sensitivity run: missing most of the next season counts as a cliff."),
        ("y_breakout", "Breakout", "top-24 WR / top-12 TE / top-24 RB in PPG with 8+ games in "
         "S+1 (Breakout population: WR/TE/RB with S - entry year 0 or 1, a game in S, not "
         "top-36 WR / top-12 TE / top-24 RB in S)",
         "A young player who becomes a fantasy starter."),
    ]  # fmt: skip
    out += [Entry(name=n, title=t, kind="label", modules=("board",), unit="boolean", formula=f,
                  explanation=e, source="twm.modules.board.populations", step="I1b")
             for n, t, f, e in labels]  # fmt: skip
    return out


def _questionable_entries() -> list[Entry]:
    """Feature #1 (twm.modules.questionable, docs/questionable.md): the injury tags and the
    two numbers the Questionable list shows."""
    from twm.modules.questionable import plays as pp

    src = "fact_injury_report.report_status (nflverse injuries)"
    mods = ("questionable",)
    return [
        Entry(
            name="questionable",
            title="Questionable",
            kind="concept",
            modules=mods,
            unit="injury tag",
            formula="fact_injury_report.report_status = 'Questionable' on the team's final "
            "injury report of the week (2016 on: the NFL's definition since dropping Probable)",
            explanation="The team says the player is uncertain to play. Since 2016 about 6 in 10 "
            "Questionable QBs, RBs, WRs and TEs played; the list shows the chance for players "
            "like him.",
            source=src,
        ),
        Entry(
            name="doubtful",
            title="Doubtful",
            kind="concept",
            modules=mods,
            unit="injury tag",
            formula="fact_injury_report.report_status = 'Doubtful' on the team's final injury "
            "report of the week",
            explanation="The team says the player is unlikely to play. Since 2016 almost none "
            "of the Doubtful QBs, RBs, WRs and TEs took an offensive snap (about 1 in 100).",
            source=src,
        ),
        Entry(
            name="practice_status",
            title="Practice status",
            kind="concept",
            modules=mods,
            unit="full / limited / did not practice / none",
            formula="fact_injury_report.practice_status of the week's final report: Full "
            "Participation, Limited Participation, Did Not Participate (blank: none)",
            explanation="How much he practiced late in the week. Shown for context only: the "
            "2025+ injury data no longer matches earlier seasons here, so the chance does not "
            "use it.",
            source="fact_injury_report.practice_status",
        ),
        Entry(
            name="play_chance",
            title="Chance he plays",
            kind="metric",
            modules=mods,
            unit="share (0-1)",
            formula="share of past tagged players with the same tag, position and 'missed his "
            "team's previous game' who took at least one offensive snap (fact_snaps), seasons "
            "2016 to last season, each group shrunk toward its parent group (20 pseudo-rows)",
            explanation="Out of 100 players like him in past seasons, about this many played. "
            "Teams name their inactive players about 90 minutes before kickoff: check then.",
            source="twm.modules.questionable (frozen table, docs/questionable.md)",
        ),
        Entry(
            name="dud_rate",
            title="Dud rate",
            kind="metric",
            modules=mods,
            unit="share (0-1)",
            formula=f"among tagged players who played, with {pp.MIN_PRIOR_GAMES}+ earlier games "
            f"and {pp.MIN_PRIOR_PPG:g}+ points per game so far: share whose points that week were "
            f"below {pp.DUD:.0%} of their points per game so far; compared with healthy players "
            "matched on season, week, position and points-per-game band",
            explanation="How often a tagged player who did play scored less than half his usual. "
            "Healthy players do that too (about 1 in 4), so compare the two numbers.",
            source="twm.modules.questionable.plays",
        ),
    ]


def _teammate_out_entries() -> list[Entry]:
    """Feature #5 (twm.modules.teammate_out, docs/teammate_out.md): the starter-out event
    and the numbers the Teammate-out list shows."""
    from twm.modules.teammate_out import history as hs
    from twm.modules.teammate_out import table as tb

    mods = ("teammate_out",)
    src = "twm.modules.teammate_out (frozen table, docs/teammate_out.md)"
    return [
        Entry(
            name="starter_out",
            title="Starter out",
            kind="concept",
            modules=mods,
            unit="event (team game)",
            formula=f"a RB with a carry share of {hs.RB_CARRY_SHARE:.0%}+ or a WR/TE with a "
            f"target share of {hs.TARGET_SHARE:.0%}+ over the games he played among his team's "
            f"previous {hs.WINDOW} (at least {hs.MIN_WINDOW_GAMES}) who takes no offensive snap "
            "while still on the team (traded, released and retired players do not count); live: "
            "Out or Doubtful on the week's injury report, or on a reserve list (IR, PUP ...)",
            explanation="One of the team's main ball carriers or pass catchers misses the game, "
            "so his carries and targets go to someone else.",
            source="fact_snaps, fact_player_week, fact_team_week, fact_roster_week, "
            "fact_injury_report",
        ),
        Entry(
            name="vacated_share",
            title="Vacated share",
            kind="metric",
            modules=mods,
            unit="share (0-1)",
            formula="the absent starter's mean carry (target) share in the team's games this "
            "season in which he played (summed when several starters are out)",
            explanation="The part of his team's running plays (passes) the missing starter "
            "usually gets: the work that is up for grabs.",
            source=src,
        ),
        Entry(
            name="allocation",
            title="Allocation",
            kind="metric",
            modules=mods,
            unit="share of the vacated share",
            formula="over past single-starter-out games (2013 on): sum of a role's share "
            "changes (game share minus his baseline share) / sum of the vacated shares; shrunk "
            f"toward its position group with {tb.PSEUDO_COUNT:g} teammate-games; carries only "
            "when a RB is out",
            explanation="How much of the missing starter's work a teammate in that role took "
            "on average: 0.46 means the backup RB got about 46% of the RB1's carries.",
            source=src,
        ),
        Entry(
            name="teammate_role",
            title="Teammate role",
            kind="concept",
            modules=mods,
            unit="label (RB2, WR3, TE1 ...)",
            formula="position + usage rank (baseline carry share + target share, then snap "
            "share) among the teammates who play; the absent starters of his position count "
            f"first; deeper than RB{hs.ROLE_CAP['RB']} / WR{hs.ROLE_CAP['WR']} / "
            f"TE{hs.ROLE_CAP['TE']} is '<pos>+'",
            explanation="Where the teammate stands in line: with the WR1 out, the next "
            "receiver is 'WR2'.",
            source="twm.modules.teammate_out.history",
        ),
        Entry(
            name="points_per_opportunity",
            title="Points per opportunity",
            kind="metric",
            modules=mods,
            unit="PPR points per carry or target",
            formula="his PPR points / (carries + targets) over his baseline games, with "
            f"{tb.PPO_PSEUDO:g} opportunities at his position's past average added",
            explanation="How much he scores with each carry or target; more work times this "
            "gives the predicted points.",
            source=src,
        ),
    ]


def _playoff_planner_entries() -> list[Entry]:
    """Feature #6 (twm.modules.playoff_planner, docs/playoff_planner.md): the matchup ratings
    of the fantasy playoff weeks and how much they matter."""
    from twm.modules.playoff_planner import backtest as bt
    from twm.modules.playoff_planner import ratings as rt

    mods = ("playoff_planner",)
    src = "twm.modules.playoff_planner (frozen rule, docs/playoff_planner.md)"
    k = ", ".join(f"{p} {v:g}" for p, v in rt.PSEUDO_GAMES.items())
    weeks = "-".join(str(w) for w in (bt.PLAYOFF_WEEKS[0], bt.PLAYOFF_WEEKS[-1]))
    return [
        Entry(
            name="fantasy_playoffs",
            title="Fantasy playoffs",
            kind="concept",
            modules=mods,
            unit="weeks",
            formula=f"NFL weeks {weeks} by default (most leagues); My League reads the owner's "
            "league settings: the weeks after the regular season, one round per playoff "
            "matchup period",
            explanation="The last weeks of the fantasy season, where a loss ends your year: "
            "the weeks a manager plans the roster for.",
            source="twm.modules.playoff_planner.league, config of the synced league",
        ),
        Entry(
            name="matchup_rating",
            title="Matchup rating",
            kind="metric",
            modules=mods,
            unit="multiplier (1.00 = average)",
            formula="points the opponent allowed to the position per game this season over the "
            "league's average per team-game, shrunk toward 1.00 with k pseudo-games (k by "
            f"position: {k}); for a D/ST, the D/ST points the opposing offense gives up; "
            "'adjusted' also divides by what the units it faced usually score",
            explanation="How friendly an opponent is: 1.10 means players at that position "
            "scored 10% more than average against it. It moves points a little; for tight "
            "ends and kickers the backtest found no gain, so they count as 1.00.",
            source=src,
        ),
        Entry(
            name="strength_of_schedule",
            title="Strength of schedule",
            kind="concept",
            modules=mods,
            unit="mean matchup rating",
            formula=f"the mean matchup rating of a player's opponents in weeks {weeks} (byes "
            "left out)",
            explanation="How easy or hard a player's coming games look as a whole: above 1.00 "
            "is an easier run than average.",
            source=src,
        ),
        Entry(
            name="matchup_gap",
            title="How much a matchup matters",
            kind="metric",
            modules=mods,
            unit="points per game",
            formula=f"walk-forward {bt.TEST_SEASONS[0]}-{bt.TEST_SEASONS[-1]}: players facing "
            "the easiest fifth of matchups (as-of raw rating) minus those facing the hardest "
            f"fifth, actual minus usual points per game in weeks {weeks}",
            explanation="The honest size of the effect: about 1 to 2 points per game for a QB, "
            "RB or WR, about 4 for a D/ST, next to nothing for a TE or kicker.",
            source="reports/playoff_planner/effects.csv",
        ),
        Entry(
            name="pseudo_games",
            title="Pseudo-games (shrinkage)",
            kind="concept",
            modules=mods,
            unit="games",
            formula="k imaginary games at exactly average added to a team's record: rating = "
            "(allowed + k x average) / ((games + k) x average); k = within-team over "
            "between-team variance, estimated on 2006-2012",
            explanation="A few games say little about a defense, so its rating starts at "
            "average and moves away only as real games pile up.",
            source=src,
        ),
    ]


def _site_entries() -> list[Entry]:
    """Terms the site uses to present the modules (how to read a chance, live or reconstructed,
    the intervals, the Report Card's calls, the board's markers), moved word for word from
    web/lib/site-terms.ts (T1, 2026-10-04) so the published glossary is their single source, and
    the metrics the pages show (ROC-AUC, PR-AUC, Brier score, log loss, MAE, PPG, games) for a
    first-season fantasy player. No numbers that belong in the database."""
    return [
        Entry(
            name="chance",
            title="Chance",
            kind="metric",
            modules=("waiver_radar",),
            unit="share (0-1), with a 90% range",
            formula="the observed y_hit rate of the backtest predictions in the same "
            "similar-players bin as his model probability: the walk-forward predictions of the "
            "same model and label from the seasons before the list's season, sorted by "
            "probability, cut into groups of at least 500 (a probability is never split) and "
            "merged so the rate never falls as the probability rises; the range is the bin's 90% "
            "Wilson interval",
            explanation="How often players the Radar rated like him became a fantasy starter "
            "soon, in earlier seasons' backtests. The range beside it is how sure that rate is. "
            "It is the track record of similar players, not a promise, and not the model's own "
            "probability (which ran too high for the top players in the backtest).",
            source="twm.modules.waiver_radar.confidence (similar_bins, Confidence.band)",
        ),
        Entry(
            name="model_probability",
            title="Model probability",
            kind="metric",
            modules=("waiver_radar",),
            unit="probability (0-1)",
            formula="the production model's predicted probability of y_hit after an isotonic "
            "calibration fit on the validation season (stored as score in the predictions store)",
            explanation="The model's own calibrated probability. The site shows the chance "
            "instead: in the backtest the model's probabilities ran too high for the most likely "
            "players.",
            source="twm.modules.waiver_radar.models",
        ),
        Entry(
            name="priority",
            title="Suggested priority",
            kind="concept",
            modules=("waiver_radar", "streamer"),
            unit="must-add / speculative / watch",
            formula="from the chance, for the top 25 of a list: must-add when it is at least "
            "0.50, speculative from 0.25 up to that, watch below; ranks 26 and lower get none. "
            "The bins only go up, so each priority is a cutoff on the model probability",
            explanation="A suggestion from the chance: must-add, speculative or watch. The "
            "cutoffs, and how often each priority hit in the backtest, are on the Methodology "
            "page.",
            source="twm.modules.waiver_radar.confidence (MUST_ADD, SPECULATIVE, tier_table)",
        ),
        Entry(
            name="list_kind",
            title="Live or reconstructed",
            kind="concept",
            modules=("shared",),
            unit="live / backtest",
            formula="live: scored on the real clock after its as-of and before the next kickoff, "
            "stored once and never rescored (append-only); backtest (reconstructed): scored later "
            "from the data public at the as-of, through the same point-in-time view",
            explanation="A live list was made in real time on the Tuesday and is never changed "
            "afterwards. A reconstructed (backtest) list was made later from the data as it stood "
            "on that Tuesday: what the Radar would have said then, not a list anyone saw at the "
            "time.",
            source="kind column of the predictions store and of the published lists",
        ),
        Entry(
            name="precision_at_10",
            title="Precision@10",
            kind="metric",
            modules=("waiver_radar",),
            unit="share (0-1)",
            formula="per weekly list: players among its top 10 with y_hit / 10 (a list with fewer "
            "than 10 players divides by its size); pooled = the mean over every list (weeks x "
            "positions) of the seasons",
            explanation="The share of a list's top 10 who became a fantasy starter soon (a hit). "
            "If you had picked up the top 10 at a position that week, it is the share that would "
            "have given you a starter week. The track record averages it over every weekly list.",
            source="twm.backtest.metrics",
        ),
        Entry(
            name="rank_bucket_hit_rate",
            title="Hit rate by rank",
            kind="metric",
            modules=("waiver_radar",),
            unit="share (0-1)",
            formula="hits / picks at ranks 1-5, 6-10 or 11-25 of the reconstructed (backtest) "
            "lists of the same position, over the seasons before the list's season",
            explanation="How often players ranked this high hit, counted over earlier "
            "reconstructed lists of the same position (seasons before this list's season, so the "
            "badge never uses outcomes the list could not have known).",
            source="twm.backtest.metrics.DEFAULT_BUCKETS; web/lib/buckets.ts",
        ),
        Entry(
            name="interval",
            title="Interval",
            kind="concept",
            modules=("shared",),
            unit="range (low to high)",
            formula="season-block bootstrap: draw as many test seasons as there are, with "
            "replacement, recompute the number on the drawn seasons, 2,000 times with a fixed "
            "seed, and keep the middle 95%; a difference between two methods is paired (both are "
            "graded on the same drawn seasons)",
            explanation="The range a number would plausibly move within if the same kind of "
            "seasons were played again. It comes from a season-block bootstrap: whole seasons are "
            "redrawn at random many times and the number is recomputed each time.",
            source="twm.backtest.metrics (block_indices, N_BOOT, LEVEL)",
        ),
        Entry(
            name="calibration",
            title="Calibration",
            kind="concept",
            modules=("shared",),
            unit="predicted against observed rate",
            formula="the predictions sorted by probability and cut into equal-count groups (10 by "
            "default); each group's mean probability is compared with the share of its rows whose "
            "outcome happened (perfect calibration: equal)",
            explanation="Whether probabilities mean what they say: among all players given a "
            "similar probability, the share who really hit should be close to that probability.",
            source="twm.backtest.metrics.calibration_bins",
        ),
        Entry(
            name="walk_forward",
            title="Walk-forward backtest",
            kind="concept",
            modules=("shared",),
            unit="method",
            formula="for each test season S: fit only on seasons before S (settings chosen on the "
            "last season before S), score every as-of of S from the data public then, no refit "
            "during S; the harness refuses any training row of S or later",
            explanation="Grading a model the honest way: every season is predicted by a model "
            "trained only on the seasons before it, using only data that was public at each "
            "Tuesday's as-of time.",
            source="twm.backtest (TestSeasonInTrainingError)",
        ),
        Entry(
            name="flex",
            title="FLEX",
            kind="concept",
            modules=("waiver_radar",),
            unit="list",
            formula="the week's RB, WR and TE picks of one kind merged: highest chance first, "
            "then the higher model probability, the better rank in his own list, RB/WR/TE and the "
            "id; each player once",
            explanation="A week's running back, wide receiver and tight end lists merged into one "
            "and ordered by chance. Each chance is the chance of a starter finish at the player's "
            "own position, so FLEX compares three slightly different targets: it is a way to "
            "browse the three lists together, not a separate model.",
            source="web/lib/flex.ts (mergeFlex)",
        ),
        Entry(
            name="listed_position",
            title="Listed position",
            kind="concept",
            modules=("waiver_radar",),
            unit="position",
            formula="nflverse's current position of the player (today's snapshot, also shown on "
            "his past rows); a Radar list ranks him at his team's roster position that week",
            explanation="The position nflverse lists the player at today, also on his past weekly "
            "rows. A Radar list ranks each player at the position of his team's roster that week, "
            "which can differ for a player who changed position.",
            source="dim_player.position",
        ),
        Entry(
            name="stream_chance",
            title="Chance (K and D/ST)",
            kind="metric",
            modules=("streamer",),
            unit="share (0-1), with a 90% range",
            formula="read off the frozen streamer backtest of the seasons before the list's "
            "season: kickers: the y_start rate of the bin of backtest picks with a similar model "
            "score (bins of at least 200 picks); D/STs: the y_start rate of the rule's picks at "
            "that rank (rank bins of at least 100 picks); a better score or rank never shows a "
            "lower chance; 90% interval",
            explanation="How often picks the streamer rated alike scored like a starter the very "
            "next week, in earlier seasons' backtests: for kickers, picks with a similar model "
            "score; for team defenses, the rule's pick at the same rank. The range beside it is "
            "how sure that rate is. A track record, not a promise.",
            source="twm.modules.streamer.confidence",
        ),
        Entry(
            name="stream_pool",
            title="Streaming pool",
            kind="concept",
            modules=("streamer",),
            unit="kickers and D/STs",
            formula="the Waiver Radar's candidate-pool rule for K and D/ST: outside the pool "
            "cutoff both in the experts' preseason ranking (last season's points per game before "
            "2020) and in points per game so far this season",
            explanation="The kickers and team defenses who are probably still on waivers: those "
            "ranked low both by the experts before the season and by points per game so far. It "
            "is an estimate, the same kind as the Waiver Radar's candidate pool, because past "
            "waiver wires are not public.",
            source="twm.modules.streamer.pool",
        ),
        Entry(
            name="garbage_time_view",
            title="With or without garbage time",
            kind="concept",
            modules=("regression_watch",),
            unit="view",
            formula="with garbage time: points, xFP and FPOE per game over every play; without: "
            "points_ng, xfp_ng and fpoe_ng per game (plays with is_garbage_time false only); the "
            "projection is computed once and shown in both",
            explanation="Points, expected points and points over expected either over every play, "
            "or only over the plays while the game was still in doubt. Stats piled up once a game "
            "is decided say little about next week. The projection itself is the same in both "
            "views.",
            source="fact_play.is_garbage_time; twm.modules.regression_watch.player_week",
        ),
        Entry(
            name="current_franchise",
            title="Team",
            kind="concept",
            modules=("shared",),
            unit="team code",
            formula="every team column holds today's franchise code (OAK -> LV, SD -> LAC, STL -> "
            "LA, LAR -> LA), with today's name",
            explanation="Teams are shown by today's franchise code and name, also for past "
            "seasons: a franchise that moved appears under its current name.",
            source="dim_team.current_abbr",
        ),
        Entry(
            name="player_season",
            title="Player-season",
            kind="concept",
            modules=("regression_watch",),
            unit="player x season",
            formula="a QB, RB, WR or TE's regular season (2009 on) with at least 8 games with an "
            "opportunity (a target, carry or pass), at the position of most of his games; his "
            "games split odd/even and first/second half",
            explanation="One player's regular season. The stability study counts each season he "
            "played enough games in once, at the position of most of his games, and splits his "
            "games into two halves.",
            source="twm.modules.regression_watch.stability (MIN_GAMES, halves)",
        ),
        Entry(
            name="stability_interval",
            title="Interval (stability study)",
            kind="concept",
            modules=("regression_watch",),
            unit="range (low to high)",
            formula="bootstrap over player-seasons: redraw them with replacement 1,000 times, "
            "recompute the split-half correlation each time and keep the middle 95%",
            explanation="The range the number would plausibly move within with other players: the "
            "player-seasons are redrawn at random many times and the number is recomputed each "
            "time. The same player appears in several seasons, so the true range is a little "
            "wider.",
            source="twm.modules.regression_watch.stability (bootstrap_corr, N_BOOT)",
        ),
        Entry(
            name="decisions_graded",
            title="Decisions",
            kind="metric",
            modules=("decisions",),
            unit="decisions",
            formula="fourth downs (2006 on) and tries after a touchdown without an exclusion (not "
            "a snap, a penalty with no play, a kneel or spike, an aborted snap, the half's last "
            "seconds, the late game, a state the models cannot score): clear calls + toss-ups",
            explanation="Every fourth down and every try after a touchdown the Report Card "
            "priced: clear calls and toss-ups together. Kneels, the half's last seconds and snaps "
            "wiped out by a penalty are left out.",
            source="twm.modules.decisions.grade (docs/decision_metrics.md)",
        ),
        Entry(
            name="clear_call",
            title="Clear call",
            kind="concept",
            modules=("decisions",),
            unit="decision",
            formula="WP(best option) - WP(second best) > decisions.toss_up_margin "
            "(config/settings.yaml), every option priced by our win-probability model of that "
            "season",
            explanation="A decision where one option's win probability beat the next best by more "
            "than the toss-up margin (Methodology page). Only clear calls are graded: a wrong one "
            "counts against the coach.",
            source="twm.modules.decisions.grade",
        ),
        Entry(
            name="toss_up",
            title="Toss-up",
            kind="concept",
            modules=("decisions",),
            unit="decision",
            formula="WP(best option) - WP(second best) <= decisions.toss_up_margin: counted, "
            "never graded",
            explanation="A decision whose best two options were within the toss-up margin of each "
            "other: the model cannot tell them apart with confidence, so it is counted but never "
            "graded, whatever the coach chose.",
            source="twm.modules.decisions.grade",
        ),
        Entry(
            name="wrong_call",
            title="Wrong call",
            kind="concept",
            modules=("decisions",),
            unit="decision",
            formula="a clear call whose chosen option is not the recommended one (the highest "
            "WP); it costs WP lost = WP(best) - WP(chosen)",
            explanation="A clear call where the coach did not choose the option with the highest "
            "win probability.",
            source="twm.modules.decisions.grade",
        ),
        Entry(
            name="clock_case",
            title="Clock case",
            kind="concept",
            modules=("decisions",),
            unit="game",
            formula="a game where timeouts_unused, half_passivity or timeout_seconds_wasted "
            "applies, by its written definition (decisions.clock in config/settings.yaml)",
            explanation="A game where one of the three clock-management metrics applies: timeouts "
            "unused in a lost one-score game, a passive end of the first half, or seconds wasted "
            "late while trailing with timeouts in hand. Each has an exact written definition; "
            "situations outside them are never graded.",
            source="twm.modules.decisions.clock",
        ),
        Entry(
            name="against_convention",
            title="Against convention",
            kind="metric",
            modules=("decisions",),
            unit="WP points",
            formula="a clear call with chosen = recommended = go (fourth down) or two-point "
            "(try); the number = WP(go) - max(WP(field goal), WP(punt)), or WP(two) - WP(kick)",
            explanation="A clear call where the aggressive option was best and the coach took it: "
            "he went for it on fourth down, or went for two. The number is how much win "
            "probability that gained over the best kicking option, by the model.",
            source="web/lib/decisions.ts (againstConvention); web/lib/queries/decisions.ts",
        ),
        Entry(
            name="hot_seat_estimate",
            title="Estimated chance",
            kind="metric",
            modules=("hot_seat",),
            unit="probability (0-1)",
            formula="the live L2 logistic regression's own probability (no recalibration) that "
            "the coach is let go (hot_seat_let_go) and it is announced from the row's day through "
            "the window's end, a set number of days after his team's final game, playoffs included",
            explanation="The model's estimate of the chance that the head coach is let go (fired "
            "during or after the season, or a mutual parting) and that it is announced within a "
            "set number of days after his team's final game. An estimate from past seasons' "
            "patterns, not a prediction that it will happen.",
            source="twm.modules.hot_seat (targets.py: the window; production.py: the pinned model)",
        ),
        Entry(
            name="hot_seat_let_go",
            title="Let go",
            kind="concept",
            modules=("hot_seat",),
            unit="yes/no",
            formula="departure type fired_in_season, fired_after_season or mutual_parting, "
            "announced in the window (the owner-verified departures file); retired, resigned "
            "(also under pressure) or left for another job: not let go",
            explanation="Fired during the season, fired after it, or a mutual parting, announced "
            "in the window. Other departures (retired, resigned, left for another job) are not "
            "counted as let go.",
            source="the owner-verified departures file (data/manual); twm.modules.hot_seat.targets",
        ),
        Entry(
            name="hot_seat_driver",
            title="Drivers",
            kind="concept",
            modules=("hot_seat",),
            unit="log-odds term",
            formula="the logistic regression's term of each input: coefficient x standardized "
            "value (a value's term and its missing-indicator term summed); the 3 largest by "
            "absolute size, signed. A row's terms sum to its log-odds minus the intercept",
            explanation="The three inputs that move this coach's estimate the most, up or down, "
            "compared with an average coach: the logistic regression's own terms (its weight "
            "times how far the value is from average).",
            source="twm.modules.hot_seat.production",
        ),
        Entry(
            name="board_cliff_chance",
            title="Chance of a Cliff",
            kind="metric",
            modules=("board",),
            unit="probability (0-1)",
            formula="the pinned Cliff model's own probability of y_cliff (an L2 logistic "
            "regression on the preseason features at the kickoff-eve as-of)",
            explanation="The model's estimated chance that the player plays enough games next "
            "season to judge and loses a large share of his points per game. It is only "
            "meaningful for a player who plays: the chance of missing time is a separate number, "
            "and the two are never added together.",
            source="twm.modules.board.production (ROLES: cliff)",
        ),
        Entry(
            name="board_missed_chance",
            title="Chance of missed time",
            kind="metric",
            modules=("board",),
            unit="probability (0-1)",
            formula="the pinned missed-time model's own probability of y_missed (a simpler "
            "logistic regression on fewer inputs, the same as-of)",
            explanation="The model's estimated chance that the player plays only a few games next "
            "season or none (injury, a benching, a release or retirement), from a separate, "
            "simpler model.",
            source="twm.modules.board.production (ROLES: missed)",
        ),
        Entry(
            name="board_ecr",
            title="Experts' preseason rank (ECR)",
            kind="metric",
            modules=("board",),
            unit="position rank",
            formula="the expert consensus rank at his position on the preseason ranking page of "
            "the last scrape before week 1 public by the board's as-of; none when he is not "
            "ranked or before the ranking archive's first season",
            explanation="FantasyPros' expert consensus ranking: many fantasy experts' preseason "
            "ranks at the position, combined into one, from the last scrape before week 1. It is "
            "the experts' consensus, not draft position (ADP), and it exists for recent seasons "
            "only.",
            source="fact_ranking.pos_rank (page_kind 'preseason')",
        ),
        Entry(
            name="board_kind",
            title="Live or reconstructed board",
            kind="concept",
            modules=("board",),
            unit="live / backtest",
            formula="live: scored on the real clock between the board's as-of (the eve of week 1) "
            "and the first kickoff, stored once (append-only); backtest: scored later by the "
            "pinned models from the input rows as they stood at the as-of",
            explanation="A live board is made before the season's first kickoff from the data "
            "public then, and never changed afterwards. A reconstructed board (backtest) was "
            "scored later, from the data as it stood on the eve of week 1: what the model would "
            "have said then, not a board anyone saw at the time.",
            source="twm.modules.board.live; twm.publish.board_lists",
        ),
        Entry(
            name="board_disagree",
            title="Where we disagree",
            kind="concept",
            modules=("board",),
            unit="marker",
            formula="ours: the top N of the board by the Cliff chance; theirs: the N players with "
            "the largest experts' rank minus last season's position rank by points per game (not "
            "ranked first); marked when on one of the two only; nothing without the experts' ranks",
            explanation="A player near the top of our list for a Cliff who is not among the same "
            "number of players the experts' ranking drops furthest below last season's finish, or "
            "the reverse. The record of past disagreements shows how both kinds turned out.",
            source="web/lib/board.ts (disagreements, BOARD_DISAGREE_TOP)",
        ),
        Entry(
            name="wp_points",
            title="WP points",
            kind="concept",
            modules=("decisions",),
            unit="percentage points (0-100)",
            formula="100 x a win probability (or a difference of two) on the 0-1 scale",
            explanation="Win probability in percentage points: one WP point is one percentage "
            "point of the team's chance to win, by our win-probability model.",
            source="twm.modules.decisions.wp",
        ),
        # ---- metrics the site shows (audit 2026-10-04: beginner tooltips) ----
        Entry(
            name="roc_auc",
            title="ROC-AUC",
            kind="metric",
            modules=("hot_seat",),
            unit="0-1 (0.5 = guessing)",
            formula="the probability that a random row whose outcome happened gets a higher "
            "probability than a random row whose outcome did not (ties count half): the area "
            "under the ROC curve, over the pooled walk-forward test rows",
            explanation="How well a model puts the cases that happened above the ones that did "
            "not. 0.5 is no better than guessing and 1 is a perfect order, so higher is better. "
            "Example: 0.80 means that in 8 of 10 pairs of one coach who was let go and one who "
            "was not, the model gave the first a higher estimate.",
            source="twm.modules.hot_seat.evaluation",
        ),
        Entry(
            name="pr_auc",
            title="PR-AUC",
            kind="metric",
            modules=("board", "hot_seat"),
            unit="0-1 (guessing = the share of cases that happened)",
            formula="average precision: the precision at each case that happened, going down the "
            "list from the highest probability, averaged over those cases (the area under the "
            "precision-recall curve)",
            explanation="How cleanly the top of a model's list is filled with the cases that "
            "really happened, for rare events. Higher is better, but guessing does not score 0.5: "
            "a random order scores the share of cases that happened. Example: if 1 player in 4 "
            "really fell off a cliff, a random order scores about 0.25, and a useful model "
            "clearly more.",
            source="twm.backtest.metrics.pr_auc (sklearn average_precision_score)",
        ),
        Entry(
            name="brier",
            title="Brier score",
            kind="metric",
            modules=("decisions", "hot_seat", "questionable"),
            unit="0-1 (lower is better)",
            formula="mean over the rows of (probability - outcome)^2, the outcome 1 when it "
            "happened and 0 when not",
            explanation="How close the probabilities came to what happened: the average squared "
            "gap between each probability and the outcome (1 if it happened, 0 if not). Lower is "
            "better and 0 is perfect. Example: saying 80% for something that happens adds 0.04; "
            "saying 80% for something that does not happen adds 0.64.",
            source="twm.backtest.metrics.brier",
        ),
        Entry(
            name="log_loss",
            title="Log loss",
            kind="metric",
            modules=("decisions", "questionable"),
            unit="0 and up (lower is better)",
            formula="mean over the rows of -[y ln(p) + (1 - y) ln(1 - p)], y = 1 when it "
            "happened, the probability p kept a hair away from 0 and 1",
            explanation="Like the Brier score, it grades probabilities against what happened, but "
            "it punishes a confident miss much harder. Lower is better. Example: saying 99% for "
            "something that does not happen costs far more than saying 60% for it.",
            source="twm.modules.decisions.wp.log_loss; twm.modules.questionable.table.log_loss",
        ),
        Entry(
            name="mae",
            title="Mean absolute error (MAE)",
            kind="metric",
            modules=("regression_watch",),
            unit="points per game (lower is better)",
            formula="mean over the graded players of |actual rest-of-season points per game - the "
            "projection|",
            explanation="How far a projection missed on average, whether it was too high or too "
            "low. Lower is better. Example: projections of 12 and 8 points per game for two "
            "players who both then scored 10 per game miss by 2 each, so the MAE is 2.",
            source="twm.modules.regression_watch.backtest (metric mae)",
        ),
        Entry(
            name="ppg",
            title="Points per game (PPG)",
            kind="metric",
            modules=("shared",),
            unit="fantasy points per game",
            formula="fantasy points (config/scoring.yaml) summed over the games counted / the "
            "number of those games (each page says which games: e.g. this season so far, or last "
            "season)",
            explanation="A player's fantasy points divided by the games he played: what he scores "
            "in a typical game. Higher is better for your team. Example: 45 points in 3 games is "
            "15 PPG.",
            source="fact_player_week (twm.scoring.score_sql)",
        ),
        Entry(
            name="games",
            title="Games (G)",
            kind="metric",
            modules=("shared",),
            unit="games",
            formula="the number of games behind the numbers beside it: regular-season games with "
            "a stat line in the window shown (on Regression Watch: this season's games up to the "
            "as-of, the games behind his PPG, xFP/game and FPOE/game)",
            explanation="How many games a player's numbers are based on. More games make a "
            "per-game number more trustworthy: a hot start over 2 games says less than a full "
            "season. Example: G 3 with 15 PPG means 45 points over 3 games.",
            source="fact_player_week; twm.modules.regression_watch.projection (player_state)",
        ),
    ]


def _entries() -> list[Entry]:
    sit = SituationRules.from_config()
    pool = _pool_texts()
    lab = _label_texts()
    return [
        # ---- fantasy basics ------------------------------------------------------------
        Entry(
            name="fantasy_points",
            title="Fantasy points",
            kind="metric",
            modules=("shared",),
            unit="points",
            formula="sum over stats of (stat value x points per unit in config/scoring.yaml); "
            "full PPR by default",
            explanation="The score a player earns for your fantasy team in one game, computed "
            "from his yards, touchdowns, catches and mistakes with your league's settings.",
            source="twm.scoring.score / score_sql on fact_player_week",
            step="B4",
            verified="the nflverse_ppr preset reproduces nflverse's fantasy_points_ppr exactly "
            "on all 150,691 QB/RB/WR/TE player-weeks 1999-2026 (tests/test_scoring.py)",
            reason_template="{player} scored {value:.1f} fantasy points",
        ),
        Entry(  # feature #2: start/sit odds (local only: it reads FantasyPros weekly ranks)
            name="start_sit_odds",
            title="Start/sit odds",
            kind="metric",
            modules=("my_league",),
            unit="share (0-1)",
            formula="P(A outscores B) for independent draws of the league points of players at "
            "A's and B's weekly expert ranks in 2020-2025 (each rank pooled with its neighbours), "
            "a tie counting one half; walk-forward checked on 2022-2025 (docs/start_sit.md)",
            explanation="Choosing between two players? This is how often a player ranked like "
            "the first outscored one ranked like the second. Under 55% it is a close call: "
            "either is a reasonable start. Local only: it uses the experts' weekly ranks, "
            "which may not be republished.",
            source="twm.modules.startsit (twm league startsit; the local weekly report)",
            step="feature #2",
        ),
        Entry(  # feature #9: luck and playoff odds (local only: the owner's ESPN league)
            name="all_play_record",
            title="All-play record",
            kind="metric",
            modules=("my_league",),
            unit="wins-losses",
            formula="over the regular-season weeks whose games are all final: each week, one win "
            "for every other team the team outscored and one loss for every team that outscored "
            "it (a tie counts as a tie)",
            explanation="Your record if you had played every team every week. It shows how good "
            "your scores were, whatever the schedule: 9-2 in a week means only two teams scored "
            "more than you.",
            source="twm.league.luck (twm league odds; the local weekly report)",
            step="feature #9",
        ),
        Entry(
            name="luck",
            title="Luck (wins above expected)",
            kind="metric",
            modules=("my_league",),
            unit="wins",
            formula="wins (a tie half) - expected wins; expected wins = the sum over the final "
            "regular-season weeks of the share of the other teams the team outscored that week "
            "(a tie half)",
            explanation="How many more games you won than your scores deserved. +1.5 means the "
            "schedule handed you about one and a half extra wins; a negative number means you "
            "lost games your points would usually win.",
            source="twm.league.luck (twm league odds; the local weekly report)",
            step="feature #9",
        ),
        Entry(
            name="playoff_odds",
            title="Playoff odds",
            kind="metric",
            modules=("my_league",),
            unit="share (0-1)",
            formula="share of 20,000 seeded simulated seasons in which the team finishes in the "
            "playoff places: every remaining matchup on the real schedule, each team's weekly "
            "score drawn around its season mean shrunk toward the league mean (6 pseudo-weeks), "
            "seeded by record then ESPN's tiebreak, then the playoff bracket (docs/my_league.md)",
            explanation="Your chance to make the playoffs, from playing out the rest of the "
            "season thousands of times on your real schedule. Early in the season it leans on "
            "the league average, so it moves a lot week to week. Local only.",
            source="twm.league.odds (twm league odds; the local weekly report)",
            step="feature #9",
        ),
        Entry(
            name="ppr",
            title="PPR (points per reception)",
            kind="concept",
            modules=("shared",),
            unit="points per catch",
            formula="config/scoring.yaml receiving.receptions (1 = full PPR, 0.5 = half, 0 = "
            "standard)",
            explanation="A scoring format that gives a point for every catch, on top of yards "
            "and touchdowns, which makes pass-catchers more valuable.",
        ),
        Entry(
            name="starter_threshold",
            title="Weekly starter threshold",
            kind="concept",
            modules=("waiver_radar", "shared"),
            unit="rank",
            formula="teams x dedicated starters at the position (derived from config/league.yaml "
            f"teams and lineup): {lab['thresholds']} by fantasy points that week; FLEX-worthy "
            "(teams x (dedicated starters + multi-position slots the position can fill), for "
            f"the positions in flex_worthy_positions): {lab['flex']}",
            explanation="A player 'finished as a starter' in a week when he scored well enough "
            f"that a typical {lab['teams']}-team league would have started him.",
            source="config/league.yaml; twm.modules.waiver_radar.labels.LabelRules",
            step="C2",
        ),
        # ---- Waiver Radar candidate pool (C1) -------------------------------------------
        Entry(
            name="candidate_pool",
            title="Waiver Radar candidate pool",
            kind="concept",
            modules=("waiver_radar",),
            unit="yes/no per player and as-of (in_pool)",
            formula="on an NFL roster at the as-of (his latest public weekly roster row of the "
            "season, on his team's latest public roster, with status "
            f"{pool['statuses']}, at QB/RB/WR/TE) and outside the top N at his position by BOTH "
            "preseason_pos_rank and ppg_to_date; N = weekly starter threshold x "
            f"candidate_pool_multiplier ({pool['multiplier']}): {pool['cutoffs']}; in seasons "
            f"without a preseason cheat sheet, rookies drafted in rounds 1-{pool['rounds']} count "
            "as drafted",
            explanation="The players who are probably still on waivers in a typical "
            f"{pool['teams']}-team league. There is no record of which players sat on fantasy "
            "rosters before 2020, so anyone ranked high before the season or scoring well "
            "since counts as taken; the rest are the players the Waiver Radar ranks.",
            source="twm.modules.waiver_radar.pool.candidate_pool (fact_roster_week, "
            "fact_ranking, fact_player_week)",
            step="C1",
        ),
        Entry(
            name="preseason_pos_rank",
            title="Preseason position rank",
            kind="feature",
            modules=("waiver_radar",),
            unit="rank (1 = best)",
            formula="2020 on (method ecr): the player's rank among his position's players on "
            "FantasyPros' last August/September redraft cheat sheet of that position before "
            "the season's first game (fact_ranking.pos_rank, page_kind preseason); not on his "
            "roster position's sheet: his rank on his own FantasyPros position's sheet, judged "
            "against that position's cutoff. 2013-2019 (method prior_ppg): his rank by last "
            "season's regular-season PPG among players of his current roster position with at "
            f"least {pool['min_games']} games; ties share the better rank",
            explanation="Where the player stood before the season: experts' consensus ranking "
            "(ECR) when it exists, otherwise last season's scoring. A player ranked high was "
            "drafted in almost every league.",
            source="fact_ranking.pos_rank; fact_player_week for last season's PPG",
            step="C1",
            reason_template=_REASONS["preseason_pos_rank"][0],
        ),
        Entry(
            name="ppg_to_date",
            title="Points per game this season",
            kind="feature",
            modules=("waiver_radar",),
            unit="points per game",
            formula="fantasy points (config/scoring.yaml) summed over the season's "
            "regular-season games public at the as-of / the number of those games (weeks with "
            "a stat line); ppg_pos_rank ranks it within the roster position (ties share the "
            "better rank)",
            explanation="How much a player has scored per game so far this season. A player "
            "near the top has been picked up by now, so he is not in the candidate pool.",
            source="fact_player_week (twm.scoring.score_sql) through twm.asof.AsOfView",
            step="C1",
            reason_template=_REASONS["ppg_to_date"][0],
        ),
        Entry(
            name="owned_avg",
            title="Rostership (percent of leagues)",
            kind="metric",
            modules=("waiver_radar",),
            unit="percent (0-100)",
            formula="FantasyPros' average share of leagues rostering the player across sites "
            "(fact_ranking.player_owned_avg) from the season's latest weekly ranking public at "
            "the as-of (the Friday before that week's games); owned_espn = ESPN's share "
            "(fact_ranking.player_owned_espn). 2020 partly, 2021 on",
            explanation="How many real leagues have the player on a roster. It checks the "
            f"candidate pool (a player owned in fewer than {pool['below']}% of leagues is really "
            "available); the pool itself never uses it, because it does not exist before 2020.",
            source="fact_ranking.player_owned_avg, fact_ranking.player_owned_espn",
            step="C1",
        ),
        # ---- the experts' view (C4 expert baseline; metrics, never features) ------------
        Entry(
            name="ecr_pos_rank",
            title="Expert rank at the as-of",
            kind="metric",
            modules=("waiver_radar",),
            unit="rank (1 = best)",
            formula="fact_ranking.pos_rank of the player on his roster position's page: the "
            "season's latest rest-of-season page public at the as-of if it lists him, else "
            "the latest weekly page listing him among those scraped up to "
            "twm.modules.waiver_radar.expert_ranks.WEEKLY_MAX_AGE_DAYS days before the "
            "latest weekly page; NULL when neither lists him (2020 on)",
            explanation="How the FantasyPros experts ranked the player at his position at the "
            "time. Used only as a baseline to beat (the 'experts' ranking); no model learns "
            "from it. Their pages were saved on Fridays, so on Tuesday they had not seen the "
            "last weekend's games yet.",
            source="fact_ranking (page_kind ros, weekly), twm.modules.waiver_radar.expert_ranks",
            step="C4",
        ),
        Entry(
            name="ecr_page_kind",
            title="Expert rank source",
            kind="metric",
            modules=("waiver_radar",),
            unit="'ros' or 'weekly'",
            formula="which page ecr_pos_rank comes from: 'ros' (rest of season) or 'weekly'; "
            "NULL when the player is unranked",
            explanation="Whether the expert rank is a rest-of-season rank or this week's rank.",
            source="twm.modules.waiver_radar.expert_ranks",
            step="C4",
        ),
        Entry(
            name="ecr_scrape_date",
            title="Expert rank date",
            kind="metric",
            modules=("waiver_radar",),
            unit="date",
            formula="scrape_date of the page ecr_pos_rank comes from (public the next day at "
            "00:00 UTC, fact_ranking.available_at)",
            explanation="The day the experts' page was saved.",
            source="fact_ranking.scrape_date",
            step="C4",
        ),
        Entry(
            name="ecr_available",
            title="Expert ranks available",
            kind="metric",
            modules=("waiver_radar",),
            unit="boolean",
            formula="a rest-of-season or weekly FantasyPros page of the player's roster position "
            "of this season is public at the as-of (the archive starts in December 2019)",
            explanation="Whether the experts' ranking existed at that moment; the expert "
            "baseline is only graded where it did.",
            source="fact_ranking",
            step="C4",
        ),
        # ---- opportunity (what a player is given) ---------------------------------------
        Entry(
            name="offense_snap_share",
            title="Snap share",
            kind="feature",
            modules=("waiver_radar", "regression_watch"),
            unit="share (0-1)",
            formula="fact_snaps.offense_pct: the player's offensive snaps divided by his "
            "team's offensive snaps in that game (Pro Football Reference, rounded to 0.01)",
            explanation="How often a player was on the field when his team had the ball. "
            "Coaches reveal their plans through snaps before the box score does.",
            source="fact_snaps.offense_pct (joined by fact_snaps.gsis_id)",
            reason_template="{player}'s snap share went from {prev:.0%} to {value:.0%}",
        ),
        Entry(
            name="target_share",
            title="Target share",
            kind="feature",
            modules=("waiver_radar", "regression_watch", "teammate_out"),
            unit="share (0-1)",
            formula="player targets / team targets in that game (nflverse player_stats)",
            explanation="The share of his team's passes thrown to a player. Targets are the "
            "raw material of receiving points.",
            source="fact_player_week.target_share",
            verified="equals targets / (sum of targets of the player's team that week) on "
            "358,395 of 358,434 player-weeks 2006-2026",
            reason_template="{player} drew {value:.0%} of his team's targets",
        ),
        Entry(
            name="air_yards_share",
            title="Air-yards share",
            kind="feature",
            modules=("waiver_radar", "regression_watch"),
            unit="share (0-1)",
            formula="player's receiving air yards / his team's air yards on all pass attempts "
            "(completions, incompletions and interceptions) in that game",
            explanation="Air yards are how far the ball travels past the line of scrimmage "
            "before it is caught or falls. A big share means a player gets the deep, valuable "
            "looks.",
            source="fact_player_week.air_yards_share",
            verified="exact on all 4,419 2025 player-weeks with air yards (denominator from "
            "fact_play)",
        ),
        Entry(
            name="wopr",
            title="WOPR (weighted opportunity rating)",
            kind="feature",
            modules=("waiver_radar", "regression_watch"),
            unit="index (about 0-1)",
            formula="1.5 x target_share + 0.7 x air_yards_share",
            explanation="One number that blends how often a player is targeted with how deep "
            "those targets are; a good summary of a receiver's opportunity.",
            source="fact_player_week.wopr",
            verified="equals the formula on every player-week 2009-2026; nflverse's values for "
            "1999-2008 do not (air-yards data is incomplete before 2006)",
        ),
        Entry(
            name="carry_share",
            title="Carry share",
            kind="feature",
            modules=("waiver_radar", "regression_watch", "teammate_out"),
            unit="share (0-1)",
            formula="player carries / team carries in that game (fact_player_week.carries / "
            "fact_team_week.carries; 0 when the team had no carry)",
            explanation="The share of his team's running plays given to a player: the "
            "running-back version of target share.",
            source="fact_player_week.carries, fact_team_week.carries",
            step="C3",
            verified="team carries equal the sum of the team's player carries on all 6,814 "
            "regular-season team-games 2013-2025; player carries equal his play-by-play runs "
            "and kneels without two-point tries on 99.996% of player-games",
        ),
        Entry(
            name="implied_team_total",
            title="Implied team total",
            kind="metric",
            modules=("shared",),
            unit="points",
            formula="home: (total_line + spread_line) / 2; away: (total_line - spread_line) / 2 "
            "(spread_line > 0 = home favored); closing lines of played games only",
            explanation="How many points the betting market expected a team to score, from "
            "the spread and the over/under. Only known right before kickoff, so P1 uses it for "
            "games already played.",
            source="fact_game.home_implied_total, fact_game.away_implied_total",
            verified="spread_line sign checked in A3 (docs/assumptions.md section 7)",
        ),
        # ---- efficiency and game state (nflfastR model columns) -------------------------
        Entry(
            name="epa",
            title="EPA (expected points added)",
            kind="metric",
            modules=("shared", "decisions", "hot_seat"),
            unit="points per play",
            formula="nflfastR's expected points after the play minus before it, for the offense",
            explanation="How much a single play helped or hurt the offense's scoring chances. "
            "A 30-yard catch on 3rd and 10 adds a lot; a sack on 1st down costs points.",
            source="fact_play.epa",
            model_output=True,
        ),
        Entry(
            name="wp",
            title="Win probability",
            kind="metric",
            modules=("shared", "decisions"),
            unit="probability (0-1)",
            formula="nflfastR's estimated probability that the team with the ball wins, "
            "before the snap",
            explanation="The chance the offense wins from this moment, given score, time, "
            "field position and more. Garbage time and neutral situations are defined from it.",
            source="fact_play.wp",
            model_output=True,
        ),
        Entry(
            name="wpa",
            title="WPA (win probability added)",
            kind="metric",
            modules=("decisions",),
            unit="probability points",
            formula="win probability after the play minus before it, for the offense",
            explanation="How much one play or decision changed a team's chance of winning.",
            source="fact_play.wpa",
            model_output=True,
        ),
        Entry(
            name="cpoe",
            title="CPOE (completion percentage over expected)",
            kind="metric",
            modules=("shared",),
            unit="percentage points",
            formula="1 if the pass was complete else 0, minus nflfastR's expected completion "
            "probability, x 100",
            explanation="Whether a quarterback completes more passes than an average passer "
            "would have on the same throws.",
            source="fact_play.cpoe",
            model_output=True,
            verified="equals 100 x (complete_pass - cp) on all 52,174 passes with a CPOE in "
            "2006, 2015 and 2025 (raw play-by-play)",
        ),
        Entry(
            name="is_garbage_time",
            title="Garbage time",
            kind="metric",
            modules=("shared", "regression_watch"),
            unit="boolean",
            formula=sit.describe_garbage_time(),
            explanation="Plays after the game is effectively decided. Stats piled up then say "
            "little about next week, so views can drop them.",
            source="fact_play.is_garbage_time (twm.situations)",
            step="B5",
            model_output=True,
        ),
        Entry(
            name="is_neutral",
            title="Neutral situation",
            kind="metric",
            modules=("shared",),
            unit="boolean",
            formula=sit.describe_neutral(),
            explanation="Plays where the game is still in the balance and neither team is "
            "forced to pass or run; the fairest view of how a team really plays.",
            source="fact_play.is_neutral (twm.situations)",
            step="B5",
            model_output=True,
        ),
        # ---- expected points and regression (Regression Watch) --------------------------
        Entry(
            name="xfp",
            title="xFP (expected fantasy points)",
            kind="feature",
            modules=("regression_watch", "waiver_radar"),
            unit="points",
            formula="sum over stats of (expected stat x points per unit in config/scoring.yaml)"
            ": the ffopportunity model's expected passing, rushing and receiving yards, "
            "touchdowns, two-point conversions, interceptions and receptions of a player-game "
            "(fact_opportunity_week *_exp columns); fumbles and return or fumble-recovery "
            "touchdowns have no expected value and add 0. Regression Watch's lists, stability "
            "study and backtest (since 2026-10-01, step H6-b2) and the player pages' weekly xFP "
            "(since 2026-10-02, step PXFP) score the same per-play expectations from the own "
            "walk-forward models instead (own_xfp); the Waiver Radar's features keep "
            "ffopportunity's",
            explanation="What an average player would have scored from the same chances "
            "(where he was targeted, where he carried the ball). Opportunity is sticky week to "
            "week.",
            source="twm.scoring.xfp / xfp_sql on fact_opportunity_week",
            step="C3",
            model_output=True,
            verified="with nflverse-PPR weights it reproduces ffopportunity's "
            "total_fantasy_points_exp within the rounding of its 2-decimal columns on every "
            "row except rushing two-point tries, whose expected yards ffopportunity adds to "
            "the points but not to rush_yards_gained_exp (docs/waiver_radar.md)",
        ),
        Entry(
            name="fpoe",
            title="FPOE (fantasy points over expected)",
            kind="feature",
            modules=("regression_watch", "waiver_radar"),
            unit="points",
            formula="fantasy_points - xfp (the same player-game; a lost fumble or a return "
            "touchdown counts fully, having no expected value); in Regression Watch since "
            "H6-b2 and on the player pages since PXFP the xfp is the own walk-forward xFP "
            "(own_xfp)",
            explanation="Points above or below what his chances were worth: partly skill, "
            "largely luck, and it tends to shrink toward zero.",
            source="twm.scoring.score_sql - twm.scoring.xfp_sql",
            step="C3",
            model_output=True,
        ),
        Entry(
            name="own_xfp",
            title="Own walk-forward xFP",
            kind="concept",
            modules=("regression_watch",),
            unit="points",
            formula="xfp from the project's own per-play models (catch chance, yards after the "
            "catch, pass touchdown and interception chances per target; yards and touchdown "
            "chance per carry; two-point success rates), LightGBM or a spline GLM per part, for "
            "a play of season S trained only on seasons 2006 to S-1; inputs are the situation "
            "before the snap (air yards, field position, down and distance, direction, quarter, "
            "score), never an nflfastR model column; scored with D1's per-play rules and "
            "config/scoring.yaml. The current season's models are pinned with their sha256 "
            "(config/production_models.yaml) and only loaded by the weekly job; the player "
            "pages' earlier seasons are frozen in the same pin (player_xfp)",
            explanation="Regression Watch's expected points since 2026-10-01 and the player "
            "pages' since 2026-10-02: the same idea as "
            "nflverse's ffopportunity, but each season is valued by models that never saw it or "
            "any later season, so a backtest cannot borrow from the future.",
            source="twm.modules.regression_watch.own_xfp",
            step="H6-b2",
        ),
        Entry(
            name="regression_to_the_mean",
            title="Regression to the mean",
            kind="concept",
            modules=("regression_watch",),
            unit="-",
            formula="extreme results drift back toward the average when luck made them extreme",
            explanation="A player who scores far above his opportunity usually comes back down; "
            "one who scores far below usually rises. Opportunity is 'stickier' than efficiency.",
        ),
        *_feature_entries(),
        *_regression_entries(),
        *_stability_entries(),
        *_projection_entries(),
        *_streamer_entries(),
        *_decisions_entries(),
        *_submodel_entries(),
        *_grade_entries(),
        *_clock_entries(),
        *_hot_seat_entries(),
        *_board_entries(),
        *_questionable_entries(),
        *_teammate_out_entries(),
        *_playoff_planner_entries(),
        *_site_entries(),
        # ---- labels (C2) ---------------------------------------------------------------
        Entry(
            name="weekly_pos_rank",
            title="Weekly position rank",
            kind="metric",
            modules=("waiver_radar",),
            unit="rank (1 = best)",
            formula="1 + the number of players of the same position with more fantasy points "
            "that regular-season week, among every player with a stat line (rank 'min': ties "
            "share the better rank); position = his point-in-time roster position that week "
            "(fact_roster_week.position; without a row that week his latest earlier roster "
            "week of the season, else that game's fact_snaps.position); QB/RB/WR/TE only "
            "(fullbacks and others are not ranked). Column pos_rank of weekly_finishes",
            explanation="Where a player's score ranked at his position that week: rank 5 at WR "
            "means four receivers scored more. Built from the finished week, so it is outcome "
            "data for labels; a feature must rank only the games public at its as-of.",
            source="twm.modules.waiver_radar.labels.weekly_finishes (fact_player_week, "
            "fact_roster_week.position, fact_snaps.position)",
            step="C2",
        ),
        Entry(
            name="is_starter_finish",
            title="Starter finish",
            kind="metric",
            modules=("waiver_radar",),
            unit="boolean",
            formula=f"weekly_pos_rank <= the position's starter threshold ({lab['thresholds']})",
            explanation="The player scored like a weekly starter in a "
            f"{lab['teams']}-team league that week.",
            source="twm.modules.waiver_radar.labels.weekly_finishes",
            step="C2",
        ),
        Entry(
            name="is_flex_finish",
            title="FLEX-worthy finish",
            kind="metric",
            modules=("waiver_radar",),
            unit="boolean",
            formula=f"{lab['flex_players'] or 'a player'} with weekly_pos_rank <= his "
            f"position's FLEX-worthy rank ({lab['flex']}); always false for "
            f"{lab['flex_never'] or 'no position'}",
            explanation=f"The {lab['flex_words'] or 'player'} scored well enough to fill a FLEX "
            "slot that week. Informative only; it is not a label.",
            source="twm.modules.waiver_radar.labels.weekly_finishes",
            step="C2",
        ),
        Entry(
            name="y_hit",
            title="Waiver hit",
            kind="label",
            modules=("waiver_radar",),
            unit="boolean (NULL while pending)",
            formula="n_starter_finishes >= 1: at least one starter finish in the label window "
            f"(the next {lab['window']} regular-season weeks after the as-of week N in which "
            "his as-of team plays; a bye is skipped and extends the window, the season's last "
            "regular-season week ends it), counting only stat lines public strictly after the "
            "as-of",
            explanation="What the Waiver Radar predicts: will this player be startable soon?",
            source="twm.modules.waiver_radar.labels.label_rows",
            step="C2",
        ),
        Entry(
            name="y_sustained",
            title="Sustained hit",
            kind="label",
            modules=("waiver_radar",),
            unit="boolean (NULL while pending)",
            formula="n_starter_finishes >= 2: as y_hit, but in at least two window weeks",
            explanation="A stricter target: a player who stays startable, not a one-week spike.",
            source="twm.modules.waiver_radar.labels.label_rows",
            step="C2",
        ),
        *[
            Entry(
                name=col,
                title=title,
                kind="label",
                modules=("waiver_radar",),
                unit=unit,
                formula=formula,
                explanation=explanation + " A label detail: never a model feature.",
                source="twm.modules.waiver_radar.labels.label_rows" + extra,
                step="C2",
            )
            for col, title, unit, formula, explanation, extra in (
                (
                    "window_weeks",
                    "Label window weeks",
                    "list of week numbers",
                    f"the first {lab['window']} regular-season weeks after the as-of week N in "
                    "which the player's as-of team has a game (its bye weeks skipped), up to the "
                    "last regular-season week (dim_week.is_last_reg_week)",
                    "The weeks whose results decide the label.",
                    " (fact_game)",
                ),
                (
                    "window_games",
                    "Games in the label window",
                    "games",
                    f"len(window_weeks): {lab['window']}, fewer in the last weeks of a season",
                    "How many games the player's team has in the window.",
                    "",
                ),
                (
                    "is_short_window",
                    "Short label window",
                    "boolean",
                    f"window_games < {lab['window']}",
                    "The window was cut short by the end of the regular season.",
                    "",
                ),
                (
                    "train_eligible",
                    "Usable for training",
                    "boolean",
                    f"window_games >= {lab['min_games']} (PROJECT_SPEC 8.1: weeks with fewer "
                    "remaining games are left out of training)",
                    "Whether a model may learn from this row.",
                    "",
                ),
                (
                    "label_status",
                    "Label status",
                    "'final' or 'pending'",
                    "'pending' until every game of every window week has a final score "
                    "(fact_game.result) and stat lines in the cache, else 'final'",
                    "A pending label (the current season) is unknown, never guessed.",
                    "",
                ),
                (
                    "n_window_played",
                    "Window weeks played",
                    "weeks",
                    "window weeks with fact_snaps.offense_snaps > 0 or a stat line, for any team",
                    "How many window weeks the player actually took the field on offense.",
                    "",
                ),
                (
                    "n_starter_finishes",
                    "Starter finishes in the window",
                    "weeks",
                    "window weeks with is_starter_finish",
                    "The count behind y_hit and y_sustained.",
                    "",
                ),
                (
                    "n_flex_finishes",
                    "FLEX-worthy finishes in the window",
                    "weeks",
                    f"window weeks with is_flex_finish ({lab['flex_positions']} only)",
                    "Informative: how often he was worth a FLEX start.",
                    "",
                ),
                (
                    "best_rank",
                    "Best weekly rank in the window",
                    "rank",
                    "min(weekly_pos_rank) over the window weeks (NULL if never ranked)",
                    "His best week at his position in the window.",
                    "",
                ),
                (
                    "window_ranks",
                    "Weekly ranks in the window",
                    "list of ranks",
                    "weekly_pos_rank of each window week (NULL where he had no stat line)",
                    "Week-by-week ranks, for reports and eyeballing.",
                    "",
                ),
                (
                    "window_points",
                    "Weekly points in the window",
                    "list of points",
                    "fantasy points of each window week (0 for a week he played without a stat "
                    "line, NULL for a week he did not play)",
                    "Week-by-week scores, for reports and eyeballing.",
                    "",
                ),
            )
        ],
        # ---- point-in-time ------------------------------------------------------------
        Entry(
            name="as_of",
            title="As-of time",
            kind="concept",
            modules=("shared",),
            unit="UTC timestamp",
            formula="dim_week.asof_weekly_utc: the first Tuesday 14:00 UTC after the Eastern "
            "date of the week's first kickoff (after Monday night for a normal week)",
            explanation="The moment a prediction is made. It may only use data that was "
            "public by then; the time machine replays any past as-of exactly.",
            source="dim_week, twm.asof",
        ),
        Entry(
            name="available_at",
            title="Available at",
            kind="concept",
            modules=("shared",),
            unit="UTC timestamp",
            formula="per table rule in twm.warehouse.available (e.g. game data: estimated game "
            "end + 6 h); later when unsure",
            explanation="When a row of data became public. A prediction at an as-of time sees "
            "only rows with available_at at or before it.",
            source="every event table's available_at column",
        ),
        # ---- identifiers: never model features (spec 6.2 rule 5) ------------------------
        *[
            Entry(
                name=col,
                title=title,
                kind="identifier",
                modules=("shared",),
                unit="id",
                formula=formula,
                explanation="An identifier: used to join tables, never as a model feature "
                "(a model would memorize who, not learn why).",
            )
            for col, title, formula in (
                ("gsis_id", "NFL player id", "nflverse's canonical player key"),
                ("player_id", "Player id (player_stats)", "the gsis_id in player_stats"),
                ("pfr_player_id", "Pro Football Reference id", "PFR player slug"),
                ("espn_id", "ESPN player id", "ESPN's player id"),
                ("game_id", "Game id", "season_week_away_home"),
                ("team", "Team code", "team abbreviation (today's code)"),
                ("posteam", "Offense team code", "team with the ball"),
                ("defteam", "Defense team code", "team on defense"),
                ("coach_id", "Coach id", "slug of the head coach's name"),
            )
        ],
    ]


def _build(entries: Iterable[Entry]) -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    for e in entries:
        if e.name in out:
            raise ValueError(f"duplicate registry entry {e.name!r}")
        out[e.name] = e
    return out


REGISTRY: dict[str, Entry] = _build(_entries())


def refresh() -> None:
    """Rebuild every entry from the current config (twm.config.reload() calls this): the texts
    quote the league shape. In place, so ``REGISTRY`` references stay valid."""
    fresh = _build(_entries())
    REGISTRY.clear()
    REGISTRY.update(fresh)


def get(name: str) -> Entry:
    try:
        return REGISTRY[name]
    except KeyError:
        close = difflib.get_close_matches(name, REGISTRY, n=3)
        hint = f"; did you mean {close}?" if close else ""
        raise KeyError(f"{name!r} is not in the registry{hint}") from None


def entries(
    *, kind: Kind | None = None, module: str | None = None, status: Status | None = None
) -> list[Entry]:
    return [
        e
        for e in REGISTRY.values()
        if (kind is None or e.kind == kind)
        and (module is None or module in e.modules or "shared" in e.modules)
        and (status is None or e.status == status)
    ]


def check_features(columns: Sequence[str], module: str) -> list[Entry]:
    """Raise FeatureCheckError unless every column is an available, registered feature usable by
    ``module``; identifiers, labels, planned entries and unknown names are all refused."""
    if module not in MODULES:
        raise ValueError(f"unknown module {module!r}; known: {MODULES}")
    problems, used = [], []
    for col in columns:
        e = REGISTRY.get(col)
        if e is None:
            close = difflib.get_close_matches(col, REGISTRY, n=1)
            hint = f" (did you mean {close[0]}?)" if close else ""
            problems.append(f"{col}: not registered{hint}")
        elif e.kind == "identifier":
            problems.append(f"{col}: an identifier can never be a feature (spec 6.2 rule 5)")
        elif e.kind != "feature":
            problems.append(f"{col}: registered as a {e.kind}, not a feature")
        elif e.status != "available":
            problems.append(f"{col}: planned for {e.step}; mark it available once its code exists")
        elif module not in e.modules and "shared" not in e.modules:
            problems.append(f"{col}: not registered for module {module}")
        else:
            used.append(e)
    if problems:
        raise FeatureCheckError("feature check failed:\n  " + "\n  ".join(problems))
    return used


_KIND_TITLES = {
    "metric": "Metrics",
    "feature": "Features",
    "label": "Labels (what models predict)",
    "concept": "Concepts",
    "identifier": "Identifiers (never model features)",
}


def glossary_markdown() -> str:
    """docs/glossary.md, generated (tests keep the committed file in sync)."""
    lines = [
        "# Glossary",
        "",
        "Generated from `src/twm/registry.py` by `uv run twm glossary --write`; do not edit by",
        "hand. Planned entries are built in the step shown. \\* = nflfastR/ffopportunity model",
        "output (PROJECT_SPEC 6.3: trained on many seasons, a mild known leak in backtests).",
        "",
    ]
    for kind, heading in _KIND_TITLES.items():
        group = sorted((e for e in REGISTRY.values() if e.kind == kind), key=lambda e: e.title)
        if not group:
            continue
        lines += [f"## {heading}", ""]
        for e in group:
            star = " \\*" if e.model_output else ""
            planned = f" (planned, step {e.step})" if e.status == "planned" else ""
            lines.append(f"### {e.title}{star}{planned}")
            lines.append("")
            lines.append(e.explanation)
            lines.append("")
            lines.append(
                f"- **Name:** `{e.name}`; **unit:** {e.unit}; **used by:** " + ", ".join(e.modules)
            )
            lines.append(f"- **Formula:** {e.formula}")
            if e.source:
                lines.append(f"- **Source:** {e.source}")
            if e.verified:
                lines.append(f"- **Verified:** {e.verified}")
            if e.reason_template:
                said = f'"{e.reason_template}"'
                if e.reason_if_false:
                    said += f' (when no: "{e.reason_if_false}")'
                who = "Streamer" if "streamer" in e.modules else "Waiver Radar"
                lines.append(f"- **{who} reason:** {said}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


FALLBACK_ABOUT = (
    "Generated from src/twm/registry.py by `uv run twm glossary --write`; do not edit by hand "
    "(tests/test_registry_site.py fails when it is out of date). The site's tooltips "
    "(web/components/Term.tsx) read the published glossary table first and this file second, so "
    "a term added to the registry has its tooltip as soon as the site deploys, before the next "
    "publish. Name, title, explanation and formula only: no reason sentences."
)


def glossary_fallback_json() -> str:
    """web/lib/glossary-fallback.json, generated: every entry's tooltip text (the same entries as
    the published glossary table), keyed by name."""
    terms = {
        e.name: {"title": e.title, "explanation": e.explanation, "formula": e.formula}
        for e in sorted(REGISTRY.values(), key=lambda e: e.name)
    }
    doc = {"about": FALLBACK_ABOUT, "terms": terms}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
