"""The scoring check (docs/scoring.md promised it; step F4): for the owner's players in each
synced week, our points (config/scoring.yaml through :mod:`twm.scoring` /
:mod:`twm.scoring_kdst`, from the warehouse's stats) against ESPN's box-score points.

Every player-week that differs by more than :data:`TOLERANCE` is listed with what explains it
where the stats we hold can say it:

- **your league's settings**: our points recomputed with the league's own scoring (the values
  `twm league settings-diff` reads from the sync), with the stats whose points change;
- **the held scoring questions** (each a flip of one choice the config made without ESPN's
  word): fumbles lost on returns (``options.fumbles_lost_scope``), a player's kick / punt return
  touchdowns, a fumble recovered for a touchdown, a kicker's blocked field goal or missed /
  blocked PAT, and D/ST points allowed counted from every point the opponent scored;
- the smallest set of those that brings our points to ESPN's (within the tolerance); none: "not
  derivable from the stats we hold" (a stat correction after the sync, a rule we do not model).

It only reports: the config is never changed. A week counts once its box scores were synced
after its last game (the regret rule). Output: NFL player names and numbers only.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from twm.league import store
from twm.league.regret import game_ends
from twm.scoring import FUMBLE_COLUMNS, STAT_KEYS, ScoringRules, score
from twm.scoring_kdst import (
    DEFENSE_COLUMNS,
    KICKING_COLUMNS,
    DefenseRules,
    KickingRules,
    score_defense,
    score_kicking,
    tier_points,
)

TOLERANCE = 0.05
SKILL = ("QB", "RB", "WR", "TE")
SKILL_EXTRA = ("fumbles_lost_total", *FUMBLE_COLUMNS["scrimmage"], "special_teams_tds",
               "fumble_recovery_tds")  # fmt: skip
KICK_EXTRA = ("pat_blocked",)
DST_EXTRA = ("points_allowed", "yards_allowed", "points_scored_against")
# the held questions, in the order the report prints them
QUESTIONS = {
    "fumbles_scope": "Fumbles lost on kick and punt returns (options.fumbles_lost_scope)",
    "return_tds": "A player's kick and punt return touchdowns (misc.special_teams_touchdowns)",
    "fumble_recovery_td": "A fumble recovered for a touchdown (misc.fumble_recovery_touchdowns)",
    "fg_blocked": "A kicker's blocked field goal (kicking.fg_blocked)",
    "pat_missed": "A kicker's missed or blocked PAT (kicking.pat_missed)",
    "dst_points_allowed": "D/ST points allowed: which points count (defense.points_allowed_tiers)",
    "dst_yards_allowed": "D/ST yards allowed (defense.yards_allowed_tiers)",
}


@dataclass(frozen=True)
class Rules:
    skill: ScoringRules
    kick: KickingRules
    dst: DefenseRules


def config_rules() -> Rules:
    return Rules(ScoringRules.from_config(), KickingRules.from_config(), DefenseRules.from_config())


def league_rules(values: Mapping[str, object], base: Rules) -> Rules:
    """``base`` with the league's values (config key -> value, settings_diff.espn_values and
    defense_values); a blocked field goal keeps the config's value (ESPN has no category)."""
    skill = {k: float(v) for k, v in values.items() if k in STAT_KEYS}  # type: ignore[arg-type]
    scope = str(values.get("options.fumbles_lost_scope", base.skill.fumbles_lost_scope))
    kick = {k.split(".", 1)[1]: float(v) for k, v in values.items()  # type: ignore[arg-type]
            if k.startswith("kicking.")}  # fmt: skip
    dst = {k.split(".", 1)[1]: float(v) for k, v in values.items()  # type: ignore[arg-type]
           if k.startswith("defense.") and not k.endswith("_tiers")}  # fmt: skip
    pa = values.get("defense.points_allowed_tiers", base.dst.points_allowed_tiers)
    ya = values.get("defense.yards_allowed_tiers", base.dst.yards_allowed_tiers)
    return Rules(
        ScoringRules.from_points({**base.skill.points, **skill}, scope),  # type: ignore[arg-type]
        base.kick.with_points(**kick),
        DefenseRules({**base.dst.points, **dst}, tuple(pa), tuple(ya)),  # type: ignore[arg-type]
    )


def synced_rules(con: duckdb.DuckDBPyConnection, base: Rules) -> Rules | None:
    """The league's rules from the last sync's scoring items (None before a sync)."""
    from twm.league.settings_diff import defense_values, espn_values, load

    synced = load(con)
    if synced is None or not synced.espn.items:
        return None
    return league_rules({**espn_values(synced.espn)[0], **defense_values(synced.espn)[0]}, base)


# ---- the warehouse's stats for the owner's player-weeks ------------------------------------

SOURCES = {"skill": ("fact_player_week", "player_id"), "K": ("fact_kicker_week", "player_id"),
           "D/ST": ("fact_defense_week", "team")}  # fmt: skip


def _needed(kind: str, rules: Sequence[Rules]) -> list[str]:
    if kind == "skill":
        cols = {c for r in rules for c in r.skill.required_columns()} | set(SKILL_EXTRA)
    elif kind == "K":
        cols = {c for cs in KICKING_COLUMNS.values() for c in cs} | set(KICK_EXTRA)
    else:
        cols = {c for cs in DEFENSE_COLUMNS.values() for c in cs} | set(DST_EXTRA)
    return sorted(cols)


def load_stats(
    warehouse: Path | None, season: int, keys: Mapping[str, list[str]], rules: Sequence[Rules]
) -> dict[str, pl.DataFrame] | None:
    """kind ('skill', 'K', 'D/ST') -> one row per (key, week) of ``season``'s regular season:
    the summed stat columns (a column the table lacks is 0). None without a warehouse."""
    if warehouse is None or not warehouse.exists():
        return None
    try:
        con = duckdb.connect(str(warehouse), read_only=True)
    except duckdb.Error:
        return None
    out: dict[str, pl.DataFrame] = {}
    try:
        for kind, ids in keys.items():
            table, key = SOURCES[kind]
            cols = _needed(kind, rules)
            try:
                have = {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
            except duckdb.Error:
                return None
            sums = ", ".join(f"sum({c})::DOUBLE AS {c}" if c in have else f"0.0 AS {c}"
                             for c in cols)  # fmt: skip
            marks = ", ".join("?" * len(ids)) or "NULL"
            out[kind] = con.execute(
                f"SELECT {key} AS key, week, {sums} FROM {table} WHERE season = ? AND "
                f"season_type = 'REG' AND {key} IN ({marks}) GROUP BY {key}, week",
                [season, *ids]).pl()  # fmt: skip
    finally:
        con.close()
    return out


def scored(kind: str, stats: pl.DataFrame, r: Rules, prefix: str) -> pl.DataFrame:
    """``stats`` with ``<prefix>points`` and the per-stat ``<prefix>fp_*`` columns."""
    if kind == "skill":
        df = score(stats, r.skill, column="points", breakdown=True)
    elif kind == "K":
        df = score_kicking(stats, r.kick, column="points", breakdown=True)
    else:
        df = score_defense(stats, r.dst, column="points", breakdown=True)
    extra = [c for c in df.columns if c == "points" or c.startswith("fp_")]
    return df.select("key", "week", *extra).rename({c: prefix + c for c in extra})


# ---- what explains a difference -----------------------------------------------------------


@dataclass(frozen=True)
class Explainer:
    question: str  # a key of QUESTIONS, or "league_settings"
    text: str
    delta: float  # what it changes in our points


def _n(s: Mapping[str, Any], col: str) -> float:
    return float(s.get(col) or 0.0)


def _flip(question: str, n: float, what: str, cur: float, alt: float) -> Explainer:
    return Explainer(question, f"{n:g} {what}: ESPN scored {alt:g} each, the config {cur:g}",
                     round((alt - cur) * n, 4))  # fmt: skip


def held(position: str, s: Mapping[str, Any], r: Rules) -> list[Explainer]:
    """The held questions this player-week's stats touch, each as the change a flip makes."""
    out: list[Explainer] = []
    if position in SKILL:
        pf = r.skill.points.get("misc.fumbles_lost", 0.0)
        ret = _n(s, "fumbles_lost_total") - sum(_n(s, c) for c in FUMBLE_COLUMNS["scrimmage"])
        if ret > 0:
            charged = r.skill.fumbles_lost_scope == "all"
            how = "did not charge it (like `scrimmage`)" if charged else "charged it (like `all`)"
            out.append(Explainer(
                "fumbles_scope", f"{ret:g} fumble(s) lost on a kick or punt return: ESPN {how}",
                round((-pf if charged else pf) * ret, 4)))  # fmt: skip
        for q, col, key, what in (
            ("return_tds", "special_teams_tds", "misc.special_teams_touchdowns",
             "kick or punt return touchdown(s)"),
            ("fumble_recovery_td", "fumble_recovery_tds", "misc.fumble_recovery_touchdowns",
             "fumble(s) recovered for a touchdown"),
        ):  # fmt: skip
            if (n := _n(s, col)) > 0:
                cur = r.skill.points.get(key, 0.0)
                out.append(_flip(q, n, what, cur, 0.0 if cur else 6.0))
    elif position == "K":
        k = r.kick.points
        if (n := _n(s, "fg_blocked")) > 0:
            cur = k.get("fg_blocked", 0.0)
            out.append(_flip("fg_blocked", n, "blocked field goal(s)", cur,
                             0.0 if cur else k.get("fg_missed", -1.0)))  # fmt: skip
        for col, what in (("pat_missed", "missed PAT(s)"), ("pat_blocked", "blocked PAT(s)")):
            if (n := _n(s, col)) > 0:
                cur = k.get("pat_missed", 0.0) if col == "pat_missed" else 0.0
                out.append(_flip("pat_missed", n, what, cur, -1.0 if cur == 0 else 0.0))
    elif position == "D/ST":
        psa, pa = s.get("points_scored_against"), s.get("points_allowed")
        if psa is not None and pa is not None and float(psa) != float(pa):
            tiers = r.dst.points_allowed_tiers
            out.append(Explainer(
                "dst_points_allowed", f"ESPN counted every point the opponent scored "
                f"({float(psa):g}, not {float(pa):g})",
                round(tier_points(psa, tiers) - tier_points(pa, tiers), 4)))  # fmt: skip
    return out


def settings_explainer(ours: Mapping[str, Any], league: Mapping[str, Any]) -> Explainer | None:
    """The league's own settings: the stats whose points change (``fp_*`` of both)."""
    delta = float(league["l_points"]) - float(ours["c_points"])
    if abs(delta) < 1e-9:
        return None
    parts = []
    for col in sorted({c[2:] for c in (*ours, *league) if c[2:].startswith("fp_")}):
        d = float(league.get("l_" + col) or 0.0) - float(ours.get("c_" + col) or 0.0)
        if abs(d) > 1e-9:
            parts.append(f"{col[3:].replace('_', ' ')} {d:+.2f}")
    return Explainer("league_settings", "your league's scoring settings (settings-diff): "
                     + ", ".join(parts), round(delta, 4))  # fmt: skip


def explain(
    espn: float, league: float, fixed: Explainer | None, cands: Sequence[Explainer]
) -> tuple[Explainer, ...] | None:
    """The smallest set of ``cands`` (on top of the league's settings) that brings ``league``
    to ``espn`` within the tolerance; None when no set of up to three does."""
    base = (fixed,) if fixed else ()
    if abs(espn - league) <= TOLERANCE:
        return base
    useful = [c for c in cands if abs(c.delta) > 1e-9]
    for k in range(1, min(3, len(useful)) + 1):
        for combo in itertools.combinations(useful, k):
            if abs(espn - league - sum(c.delta for c in combo)) <= TOLERANCE:
                return base + combo
    return None


# ---- the check -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerWeek:
    week: int
    espn_id: int
    name: str
    position: str
    espn: float  # ESPN's box-score points
    ours: float  # config/scoring.yaml
    league: float  # the league's own settings (= ours without a synced scoring)
    present: tuple[str, ...]  # the held questions this week's stats touch (a non-zero flip)
    explained_by: tuple[Explainer, ...] | None  # None: not derivable; (): no difference

    @property
    def diff(self) -> float:
        return round(self.espn - self.ours, 2)

    @property
    def differs(self) -> bool:
        return abs(self.espn - self.ours) > TOLERANCE


@dataclass
class ScoringCheck:
    season: int
    weeks: list[int]  # the final weeks compared
    rows: list[PlayerWeek]  # every player-week compared
    no_stats: list[tuple[int, str]] = field(default_factory=list)  # ESPN points, no stats
    unlinked: list[tuple[int, str]] = field(default_factory=list)  # no gsis id / D/ST team
    left_out: list[int] = field(default_factory=list)  # synced before the games were over
    league_scoring: bool = False  # the league's own scoring was synced
    yards_tiers: tuple = ()  # the league's D/ST yards-allowed tiers
    config_yards_tiers: tuple = ()

    @property
    def differing(self) -> list[PlayerWeek]:
        return [r for r in self.rows if r.differs]


class ScoringCheckUnavailableError(LookupError):
    """No box scores, no owner's team or no warehouse: one plain line."""


BOX_SQL = """
    SELECT week, synced_at, espn_id, player_name, position, gsis_id, entity_id, points
    FROM league_box_scores WHERE league_id = ? AND season = ? AND league_team_id = ?
    ORDER BY week, espn_id
"""


def _kind(position: str) -> str | None:
    return "skill" if position in SKILL else (position if position in ("K", "D/ST") else None)


def _key(kind: str, gsis: str | None, entity: str | None) -> str | None:
    if kind == "D/ST":
        return entity.removeprefix(store.DST_PREFIX) if entity else None
    return gsis


def check(con: duckdb.DuckDBPyConnection, warehouse: Path | None) -> ScoringCheck:
    """Every final synced week of the latest box-score season, for the owner's team."""
    part = store.latest(con, "league_box_scores")
    if part is None:
        raise ScoringCheckUnavailableError(
            "no box scores synced yet (`uv run twm league sync --week N` for a past week)"
        )
    lid, season = part.league_id, part.season
    team = store.my_team(con, lid, season)
    if team is None:
        raise ScoringCheckUnavailableError("your team was not identified in the last sync")
    if warehouse is None or not warehouse.exists():
        raise ScoringCheckUnavailableError("no warehouse: our points cannot be computed")
    base = config_rules()
    league = synced_rules(con, base)
    rules = league or base
    current = int(store.settings_of(con, lid, season).get("current_week") or 0)
    ends = game_ends(warehouse, season)
    lines = con.execute(BOX_SQL, [lid, season, team]).fetchall()
    out = ScoringCheck(season, [], [], league_scoring=league is not None,
                       yards_tiers=tuple(rules.dst.yards_allowed_tiers),
                       config_yards_tiers=tuple(base.dst.yards_allowed_tiers))  # fmt: skip
    final: list[tuple] = []
    for week in sorted({int(r[0]) for r in lines}):
        rows = [r for r in lines if int(r[0]) == week]
        synced = max(r[1] for r in rows)
        ok = synced >= ends[week] if ends is not None and week in ends else week < current
        if ok:
            final += rows
            out.weeks.append(week)
        else:
            out.left_out.append(week)
    keys: dict[str, set[str]] = {}
    for r in final:
        kind = _kind(str(r[4] or ""))
        k = _key(kind, r[5], r[6]) if kind else None
        if kind and k:
            keys.setdefault(kind, set()).add(k)
    stats = load_stats(warehouse, season, {k: sorted(v) for k, v in keys.items()}, [base, rules])
    if stats is None:
        raise ScoringCheckUnavailableError("the warehouse's stat tables cannot be read")
    table: dict[tuple[str, str, int], dict[str, Any]] = {}
    for kind, df in stats.items():
        both = df.join(scored(kind, df, base, "c_"), on=["key", "week"]).join(
            scored(kind, df, rules, "l_"), on=["key", "week"])  # fmt: skip
        table |= {(kind, str(d["key"]), int(d["week"])): d for d in both.iter_rows(named=True)}
    for week, _, eid, name, pos, gsis, ent, pts in final:
        kind = _kind(str(pos or ""))
        if pts is None or kind is None:
            continue
        k = _key(kind, gsis, ent)
        who = (int(week), str(name or eid))
        if k is None:
            out.unlinked.append(who)
            continue
        s = table.get((kind, k, int(week)))
        if s is None:
            if abs(float(pts)) > TOLERANCE:
                out.no_stats.append(who)
            else:  # no game, no points: nothing to compare but it agrees
                out.rows.append(PlayerWeek(int(week), int(eid), who[1], str(pos), float(pts),
                                           0.0, 0.0, (), ()))  # fmt: skip
            continue
        ours, lg = round(float(s["c_points"]), 4), round(float(s["l_points"]), 4)
        cands = held(str(pos), s, rules)
        present = tuple(c.question for c in cands if abs(c.delta) > 1e-9)
        if pos == "D/ST":
            present += ("dst_yards_allowed",)
        row = PlayerWeek(int(week), int(eid), who[1], str(pos), float(pts), ours, lg, present, ())
        if row.differs:
            fixed = settings_explainer(s, s) if league is not None else None
            row = replace(row, explained_by=explain(float(pts), lg, fixed, cands))
        out.rows.append(row)
    return out


def _tiers_text(tiers: Sequence) -> str:
    return ", ".join(f"up to {b}: {p:g}" if b is not None else f"more: {p:g}" for b, p in tiers)


def evidence(res: ScoringCheck) -> list[tuple[str, str]]:
    """(held question, what the synced weeks say about it), in plain words; never a config
    change, only what to consider."""
    out = []
    for q, title in QUESTIONS.items():
        cases = [r for r in res.rows if q in r.present]
        if q == "dst_yards_allowed":
            league, cfg = res.yards_tiers, res.config_yards_tiers
            said = (f"your league scores yards allowed ({_tiers_text(league)})" if league else
                    "your league does not score yards allowed")  # fmt: skip
            same = "the config does the same" if tuple(league) == tuple(cfg) else (
                "the config does not: see the settings-diff patch")  # fmt: skip
            if not res.league_scoring:
                said, same = "the league's scoring is not synced", "nothing to compare"
            ok = sum(1 for r in cases if r.explained_by is not None)
            out.append((title, f"{said}; {same}. {len(cases)} D/ST week(s) compared, {ok} of "
                        "them explained."))  # fmt: skip
            continue
        if not cases:
            out.append((title, "no evidence yet: none of your players had such a play in the "
                        "synced weeks."))  # fmt: skip
            continue
        flipped = [r for r in cases if r.explained_by and any(
            e.question == q for e in r.explained_by)]  # fmt: skip
        unclear = [r for r in cases if r.explained_by is None]
        same = len(cases) - len(flipped) - len(unclear)
        text = (
            f"{len(cases)} player-week(s): ESPN scored {same} the way the config does and "
            f"{len(flipped)} the other way" + (f", {len(unclear)} unclear" if unclear else "")
        )
        if flipped and not same:
            text += ". Your league does it the other way: consider changing config/scoring.yaml."
        elif same and not flipped:
            text += ". The config matches your league."
        else:
            text += ". Not settled: check those weeks by hand."
        out.append((title, text))
    return out
