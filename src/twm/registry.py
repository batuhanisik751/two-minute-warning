"""Feature and metric registry (PROJECT_SPEC 11 B6): one entry per number the app shows or feeds
to a model, with its formula, a beginner-friendly explanation and its unit.

Uses:
- UI tooltips and the glossary (`twm glossary`, docs/glossary.md, generated from this file);
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
import re
import string
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from twm.config import league
from twm.situations import SituationRules

Kind = Literal["metric", "feature", "label", "concept", "identifier"]
Status = Literal["available", "planned"]
MODULES = (
    "shared",
    "waiver_radar",
    "regression_watch",
    "my_league",
    "decisions",
    "hot_seat",
    "board",
)
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
# Placeholders a reason template may use (C6 passes these values).
REASON_FIELDS = ("player", "value", "prev", "delta", "weeks", "teammate", "team")


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
    reason_template: plain-English sentence with {placeholders} from REASON_FIELDS (for C6).
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
        if self.reason_template is not None:
            fields = {f for _, f, _, _ in string.Formatter().parse(self.reason_template) if f}
            unknown = {f.split(".")[0].split("[")[0] for f in fields} - set(REASON_FIELDS)
            if unknown:
                problems.append(f"reason_template uses unknown fields {sorted(unknown)}")
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
    return {
        "thresholds": ", ".join(f"{p} top {n}" for p, n in lg.starter_rank_threshold.items()),
        "flex": str(lg.flex_worthy_rank),
        "window": str(WINDOW_GAMES),
        "min_games": str(MIN_TRAIN_GAMES),
    }


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
         "{player} played {value:.0%} of the snaps last game"),
        ("snap_share_avg3", "Snap share, last 3 games", "share (0-1)",
         f"mean offense_snap_share over the team's last 3 games ({win})",
         "His usual playing time lately, less noisy than one game.", "fact_snaps.offense_pct",
         "{player} averaged {value:.0%} of the snaps over the last {weeks} games"),
        ("snap_share_season", "Snap share, season", "share (0-1)",
         f"mean offense_snap_share over all team games of the season ({win})",
         "His playing time over the whole season so far.", "fact_snaps.offense_pct", None),
        ("snap_share_delta", "Snap share change", "share points",
         "snap_share_last minus the mean snap share of the up to 3 team games before the last "
         "(NULL when the last game is the team's first)",
         "Did his playing time just go up or down? A big rise means the coaches trust him "
         "more.", "fact_snaps.offense_pct",
         "{player}'s snap share went from {prev:.0%} to {value:.0%}"),
        ("target_share_last", "Target share, last game", "share (0-1)",
         f"nflverse target_share in the team's last game, 0 without a stat line ({win})",
         "The share of his team's passes thrown his way last game.",
         "fact_player_week.target_share", "{player} drew {value:.0%} of the targets last game"),
        ("target_share_avg3", "Target share, last 3 games", "share (0-1)",
         f"mean nflverse target_share over the team's last 3 games ({win})",
         "How much of the passing game goes to him lately.", "fact_player_week.target_share",
         "{player} drew {value:.0%} of his team's targets over the last {weeks} games"),
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
         "{player} got {value:.0%} of the carries last game"),
        ("carry_share_avg3", "Carry share, last 3 games", "share (0-1)",
         f"mean carry_share over the team's last 3 games ({win})",
         "How much of the running game goes to him lately.",
         "fact_player_week.carries, fact_team_week.carries",
         "{player} got {value:.0%} of his team's carries over the last {weeks} games"),
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
         "fact_play", "{player} had {value:.1f} goal-line chances per game lately"),
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
         "{player}'s chances were worth {value:.1f} fantasy points per game lately"),
        ("fpoe_avg3", "FPOE, last 3 games", "points per game",
         f"mean over the team's last 3 games of (fantasy_points - xfp) ({win})",
         "Scoring above or below his chances lately; mostly luck, so it is weighted low.",
         "fact_player_week, fact_opportunity_week", None),
        ("fantasy_points_last", "Fantasy points, last game", "points",
         f"fantasy points (config/scoring.yaml) in the team's last game ({win})",
         "What he scored last game.", "fact_player_week (twm.scoring.score_sql)",
         "{player} scored {value:.1f} fantasy points last game"),
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
         "{player} moved from {prev:.0f} to {value:.0f} on the depth chart"),
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
         "{teammate} is out, leaving {value:.0%} of the targets"),
        ("vacated_carry_share", "Vacated carry share", "share (sum)",
         "as vacated_target_share, with carry_share", "Carries freed up by teammates who "
         "are out.", "fact_player_week, fact_team_week, fact_roster_week",
         "{teammate} is out, leaving {value:.0%} of the carries"),
        ("same_pos_vacated_target_share", "Vacated target share at his position",
         "share (sum)",
         "vacated_target_share over unavailable teammates of his own position group only",
         "Targets freed up by players at his own position: the most direct path to more "
         "work.", "as vacated_target_share",
         "{teammate} ({value:.0%} of the targets) is out at his position"),
        ("same_pos_vacated_carry_share", "Vacated carry share at his position",
         "share (sum)",
         "vacated_carry_share over unavailable teammates of his own position group only",
         "Carries freed up by players at his own position.", "as vacated_carry_share",
         "{teammate} ({value:.0%} of the carries) is out at his position"),
        ("teammate_same_pos_unavailable", "Teammates out at his position", "players",
         "number of unavailable teammates of his position group", "How many players at his "
         "position are out.", "as vacated_target_share", None),
        ("top_teammate_out", "A player ahead of him is out", "boolean",
         "an unavailable same-position teammate averaged a higher snap share than he did "
         "over the same games (the teammate's last 3 team games up to his last appearance)",
         "Someone who played ahead of him is out: the classic waiver opportunity.",
         "fact_snaps, as vacated_target_share",
         "{teammate}, who played ahead of {player}, is out"),
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
         "{player}'s next opponents allow {value:.2f} times the average to his position"),
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
            reason_template=template,
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
            formula="teams x starters at the position (config/league.yaml "
            f"starter_rank_threshold): {lab['thresholds']} by fantasy points that week; "
            f"FLEX-worthy (flex_worthy_rank): RB/WR top {lab['flex']}",
            explanation="A player 'finished as a starter' in a week when he scored well enough "
            "that a typical 12-team league would have started him.",
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
            explanation="The players who are probably still on waivers in a typical 12-team "
            "league. There is no record of which players sat on fantasy rosters before 2020, so "
            "anyone ranked high before the season or scoring well since counts as taken; the "
            "rest are the players the Waiver Radar ranks.",
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
            modules=("waiver_radar", "regression_watch"),
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
            modules=("waiver_radar", "regression_watch"),
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
            "touchdowns have no expected value and add 0",
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
            "touchdown counts fully, having no expected value)",
            explanation="Points above or below what his chances were worth: partly skill, "
            "largely luck, and it tends to shrink toward zero.",
            source="twm.scoring.score_sql - twm.scoring.xfp_sql",
            step="C3",
            model_output=True,
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
            explanation="The player scored like a weekly starter in a 12-team league that week.",
            source="twm.modules.waiver_radar.labels.weekly_finishes",
            step="C2",
        ),
        Entry(
            name="is_flex_finish",
            title="FLEX-worthy finish",
            kind="metric",
            modules=("waiver_radar",),
            unit="boolean",
            formula=f"a RB or WR with weekly_pos_rank <= flex_worthy_rank ({lab['flex']}); "
            "always false for QB and TE",
            explanation="The running back or receiver scored well enough to fill a FLEX slot "
            "that week. Informative only; it is not a label.",
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
                    "window weeks with is_flex_finish (RB/WR only)",
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
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
