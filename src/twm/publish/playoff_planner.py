"""The playoff planner as ``twm publish`` writes it (feature #6; docs/playoff_planner.md). Reads
only.

Sources (opened read-only):

- **snapshots**: the predictions store's ``playoff_planner_snapshots`` (written by ``twm
  playoff_planner weekly``), published append-only (:data:`twm.publish.tables.PLAYOFF_PLANNER`):
  one ``playoff_planner_list`` row per (season, through_week) and its grid in
  ``playoff_planner_row`` (every team x playoff week x QB / RB / WR / TE / K / DST). Keyed by
  (season, through_week), never by as_of: the nightly runner's store starts empty and stores
  the same completed week every night until the next one completes; the first published copy
  of a week wins and is never rewritten;
- **the pinned spec** (:func:`twm.modules.playoff_planner.production.load_pinned`, sha256
  checked before it is read): its model_versions row (``params``: the choice and its
  disclosure), the chosen candidate per position (``playoff_planner_choice``), the candidates'
  horizon-pooled backtest with the test seasons each won against the choice it was compared
  with (``playoff_planner_backtest``: ``chosen`` = the one used, ``rule_pick`` = the rule's),
  the effect sizes, the stability and the late-weeks shares (``playoff_planner_effects`` /
  ``_stability`` / ``_late_weeks``, as reports/playoff_planner/*.csv);
- **live record** (``playoff_planner_live``): per position and 'all', each (week, team,
  position)'s last snapshot rated before that week, graded with the warehouse's unit points
  (:func:`twm.modules.playoff_planner.weekly.grade` / ``summary``) over the published
  snapshots plus the night's (:func:`live_record`). Empty until a game of the season's last
  playoff week is in the warehouse: the record is read after the fantasy playoffs.

No FantasyPros data and no league data: everything here is public.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm.publish.collect import PublishInputError
from twm.publish.questionable import UTC_TS, _empty, _utc
from twm.publish.tables import PLAYOFF_PLANNER, TABLES

MODULE = "playoff_planner"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
CANDIDATES = ("none", "raw", "shrunk", "adjusted")
TITLES = {"none": "No matchup (every game 1.00)", "raw": "Raw rating",
          "shrunk": "Shrunk rating", "adjusted": "Schedule-adjusted rating"}  # fmt: skip
ALL = "all"
# playoff_planner_row's columns the live record grades (weekly.grade), plus the list's as_of
GRADED = ("season", "through_week", "week", "team", "position", "opponent", "candidate",
          "rating", "lg_ppg")  # fmt: skip
ACTUALS = {"season": pl.Int32, "week": pl.Int32, "game_id": pl.String, "team": pl.String,
           "opponent": pl.String, "position": pl.String, "points": pl.Float64}  # fmt: skip
INTS = ("season", "through_week", "week", "rating_rank", "opp_games")
# the seasons the pinned pseudo-games were estimated on (ratings.PSEUDO_GAMES; before every test
# season): model_versions.training_seasons
K_SEASONS = (2006, 2012)


@dataclass
class PlayoffPlannerData:
    """The module's part of a publish: ``lists`` / ``rows`` (every stored snapshot; append-only
    in the target), ``tables`` (its replaced tables by name), ``versions`` (the pinned spec's
    model_versions row, published layout), the pinned ``season`` and its playoff ``weeks``."""

    season: int
    version: str
    lists: pl.DataFrame
    rows: pl.DataFrame
    tables: dict[str, pl.DataFrame] = field(default_factory=dict)
    versions: pl.DataFrame | None = None
    # the season's (team-game, position) points (weekly.read_actuals): the live record
    actuals: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=ACTUALS))
    weeks: tuple[int, ...] = (15, 16, 17)


def _order(df: pl.DataFrame) -> pl.Expr:
    """QB, RB, WR, TE, K, DST (then 'all')."""
    order = {p: i for i, p in enumerate((*POSITIONS, ALL))}
    return pl.col("position").replace_strict(order, default=len(order), return_dtype=pl.Int32)


def snapshot_frames(snaps: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(playoff_planner_list, playoff_planner_row) of the store's snapshot rows
    (weekly.COLUMNS): one list per (season, through_week); its rows by week, team, position."""
    if snaps.is_empty():
        return _empty(PLAYOFF_PLANNER.lists), _empty(PLAYOFF_PLANNER.rows)
    df = _utc(snaps.rename({"kickoff_utc": "kickoff"}), "as_of", "kickoff", "created_at")
    df = df.with_columns(pl.col(c).cast(pl.Int32) for c in INTS)
    key = list(PLAYOFF_PLANNER.key)
    lists = df.group_by(key).agg(
        pl.col("as_of").unique().alias("_a"), pl.col("model_version").unique().alias("_v"),
        pl.col("created_at").max().alias("generated_at"),
        pl.col("team").n_unique().cast(pl.Int32).alias("n_teams"),
        pl.len().cast(pl.Int32).alias("n_rows"),
    ).sort(key)  # fmt: skip
    odd = lists.filter((pl.col("_a").list.len() != 1) | (pl.col("_v").list.len() != 1))
    if odd.height:
        r = odd.row(0, named=True)
        raise PublishInputError(f"the playoff-planner snapshot {r['season']} through week "
                                f"{r['through_week']} mixes as-ofs or model versions")  # fmt: skip
    lists = lists.with_columns(pl.col("_a").list.first().alias("as_of"),
                               pl.col("_v").list.first().alias("model_version"))  # fmt: skip
    rows = df.with_columns(_order(df).alias("_o")).sort([*key, "week", "team", "_o"])
    return (lists.select(TABLES[PLAYOFF_PLANNER.lists].names),
            rows.select(TABLES[PLAYOFF_PLANNER.rows].names))  # fmt: skip


def _mae(part: pl.DataFrame, candidate: str) -> dict[str, float]:
    x = part.filter(pl.col("candidate") == candidate)
    return dict(zip(x["season"].to_list(), x["mae"].to_list(), strict=True))


def backtest_rows(content: dict[str, Any]) -> pl.DataFrame:
    """playoff_planner_backtest: per position, each candidate's MAE pooled over the horizons and
    test seasons, and the fixed rule replayed on the pinned per-season MAEs: the choice it was
    compared with (``vs``), the test seasons it beat it in (``seasons_won``), whether it took
    over (``took_over``: on the rule's path; 'none' is the start). Refused when the replay
    differs from the pin's ``path`` / ``rule_choice``."""
    from twm.modules.playoff_planner import production as pr

    bt = pr.report_frames(content)["backtest"].filter(pl.col("horizon") == ALL)
    bt = bt.with_columns(pl.col("season").cast(pl.String))
    needed, tests = int(content["season_wins_needed"]), len(content["test_seasons"])
    out = []
    for pos in content["positions"]:
        part = bt.filter(pl.col("position") == pos)
        cur, path = CANDIDATES[0], [CANDIDATES[0]]
        for c in content["candidates"]:
            a = _mae(part, c)
            if ALL not in a:
                continue
            n = part.filter((pl.col("candidate") == c) & (pl.col("season") == ALL))["n"][0]
            row = {"position": pos, "candidate": c, "title": TITLES.get(c, c), "n": int(n),
                   "mae": float(a[ALL]), "vs": None, "seasons_won": None, "seasons": tests,
                   "took_over": c == CANDIDATES[0]}  # fmt: skip
            if c != CANDIDATES[0]:
                b = _mae(part, cur)
                won = sum(a[s] < b[s] for s in a if s != ALL and s in b)
                row.update({"vs": cur, "seasons_won": int(won)})
                if a[ALL] < b[ALL] and won >= needed:
                    cur, row["took_over"] = c, True
                    path.append(c)
            out.append(row)
        if cur != content["rule_choice"][pos] or path != list(content["path"][pos]):
            raise PublishInputError(f"the playoff planner's rule replayed on the pinned backtest "
                                    f"picks {pos} {cur}, the pin says "
                                    f"{content['rule_choice'][pos]}")  # fmt: skip
    df = pl.DataFrame(out, schema=_empty("playoff_planner_backtest").drop("chosen", "rule_pick")
                      .schema)  # fmt: skip
    chosen = pl.col("position").replace_strict(content["chosen"], default=None)
    rule = pl.col("position").replace_strict(content["rule_choice"], default=None)
    return df.with_columns((pl.col("candidate") == chosen).alias("chosen"),
                           (pl.col("candidate") == rule).alias("rule_pick"))  # fmt: skip


def choice_rows(content: dict[str, Any]) -> pl.DataFrame:
    """playoff_planner_choice: per position the candidate used, the rule's, who chose it, the
    rule's path ('none > shrunk > adjusted') and the pseudo-games."""
    rows = [{"position": p, "candidate": str(content["chosen"][p]),
             "rule_choice": str(content["rule_choice"][p]), "chosen_by": str(content["chosen_by"]),
             "path": " > ".join(content["path"][p]),
             "pseudo_games": float(content["pseudo_games"][p])}
            for p in content["positions"]]  # fmt: skip
    return pl.DataFrame(rows, schema=_empty("playoff_planner_choice").schema)


def pin_tables(content: dict[str, Any]) -> dict[str, pl.DataFrame]:
    """playoff_planner_choice, _backtest (:func:`backtest_rows`), _effects, _stability and
    _late_weeks from the pinned JSON's content (as reports/playoff_planner/*.csv)."""
    from twm.modules.playoff_planner import production as pr

    fr = pr.report_frames(content)
    sch = {n: _empty(f"playoff_planner_{n}").schema for n in ("effects", "stability",
                                                              "late_weeks")}  # fmt: skip
    eff = fr["effects"].with_columns(pl.col("horizon").cast(pl.String))
    out = {"playoff_planner_choice": choice_rows(content),
           "playoff_planner_backtest": backtest_rows(content)}  # fmt: skip
    for n, f in (("effects", eff), ("stability", fr["stability"]),
                 ("late_weeks", fr["late_weeks"])):  # fmt: skip
        f = f.select(pl.col(c).cast(t) for c, t in sch[n].items())
        out[f"playoff_planner_{n}"] = f.sort(_order(f), *f.columns[1:2]) if n != "late_weeks" \
            else f.sort("era", _order(f), "week")  # fmt: skip
    return out


PARAMS = ("chosen", "rule_choice", "chosen_by", "rule", "path", "candidates", "pseudo_games",
          "test_seasons", "horizons", "playoff_weeks", "season_wins_needed", "min_games",
          "min_base")  # fmt: skip


def version_row(content: dict[str, Any], created_at: datetime) -> pl.DataFrame:
    """The pinned spec's model_versions row (published layout, collect.version_rows): its
    ``params`` carry the choice and its disclosure (:data:`PARAMS`)."""
    from twm.modules.playoff_planner import production as pr

    params = {k: content[k] for k in PARAMS}
    return pl.DataFrame(
        [{"model_version": str(content["model_version"]), "module": MODULE, "model": pr.MODEL,
          "label": pr.LABEL, "training_seasons": list(range(K_SEASONS[0], K_SEASONS[1] + 1)),
          "test_season": int(content["season"]),
          "feature_list": ["opponent_points_allowed", "position"],
          "params": json.dumps(params, sort_keys=True), "created_at": created_at}],
        schema={"model_version": pl.String, "module": pl.String, "model": pl.String,
                "label": pl.String, "training_seasons": pl.List(pl.Int32),
                "test_season": pl.Int32, "feature_list": pl.List(pl.String),
                "params": pl.String, "created_at": UTC_TS},
    )  # fmt: skip


def live_rows(graded: pl.DataFrame, done: bool, season: int) -> pl.DataFrame:
    """playoff_planner_live from weekly.grade's rows: weekly.summary per position and 'all'
    (n graded, pending, MAE of the realized multiplier vs the rating and vs a flat 1.00), plus
    the playoff weeks with a graded game (``weeks``). Empty unless ``done`` (a game of the
    season's last playoff week is in): the record is read after the fantasy playoffs."""
    from twm.modules.playoff_planner import weekly as wk

    empty = _empty("playoff_planner_live")
    if not done or graded.is_empty():
        return empty
    played = graded.filter(pl.col("outcome").is_not_null())
    weeks = pl.concat([played, played.with_columns(pl.lit(ALL).alias("position"))]).group_by(
        "position").agg(pl.col("week").n_unique().cast(pl.Int32).alias("weeks"))  # fmt: skip
    s = wk.summary(graded).join(weeks, on="position", how="left")
    s = s.with_columns(pl.lit(int(season), pl.Int32).alias("season"),
                       pl.col("weeks").fill_null(0))  # fmt: skip
    return s.select(pl.col(c).cast(t) for c, t in empty.schema.items())


def no_published() -> pl.DataFrame:
    """:func:`published_rows` of a target holding no snapshot."""
    return _empty(PLAYOFF_PLANNER.rows).select(GRADED).with_columns(
        pl.lit(None, UTC_TS).alias("as_of"))  # fmt: skip


def published_rows(conn: Any, season: int) -> pl.DataFrame:
    """The ``playoff_planner_row`` rows of ``season`` already in the target (``conn``: an open
    psycopg connection; reads only), the columns the live record grades (:data:`GRADED`) and
    their snapshot's as_of."""
    cols = ", ".join(f'r."{c}"' for c in GRADED)
    got = conn.execute(
        f'SELECT {cols}, l."as_of" FROM "{PLAYOFF_PLANNER.rows}" r JOIN '
        f'"{PLAYOFF_PLANNER.lists}" l ON l.season = r.season AND l.through_week = r.through_week '
        "WHERE r.season = %s", [int(season)]).fetchall()  # fmt: skip
    return pl.DataFrame(got, schema=no_published().schema, orient="row")


def record_snapshots(d: PlayoffPlannerData, published: pl.DataFrame) -> pl.DataFrame:
    """The snapshot rows of ``d.season`` the live record grades (weekly.grade's layout): the
    ``published`` rows plus ``d``'s local ones of the snapshots not published yet.
    Append-only: a published (season, through_week) wins over a local one."""
    key, this = list(PLAYOFF_PLANNER.key), pl.col("season") == int(d.season)
    cols = [*GRADED, "as_of"]
    mine = d.rows.filter(this).join(d.lists.select(*key, "as_of"), on=key).select(cols)
    published = published.filter(this).select(cols)
    new = mine.join(published.select(key).unique(), on=key, how="anti")
    both = pl.concat([published, new], how="vertical_relaxed")
    return both.sort(*key, "week", "team", "position")


def live_record(d: PlayoffPlannerData, published: pl.DataFrame) -> pl.DataFrame:
    """``playoff_planner_live`` as the target will hold it after the publish: :func:`live_rows`
    of :func:`record_snapshots`, graded with the season's unit points (``d.actuals``)."""
    from twm.modules.playoff_planner import weekly as wk

    actuals = d.actuals.with_columns(pl.col("season", "week").cast(pl.Int32))
    done = actuals.filter(pl.col("week") >= max(d.weeks)).height > 0
    return live_rows(wk.grade(record_snapshots(d, published), actuals), done, d.season)


def collect_playoff_planner(store: Path, warehouse: Path, season: int,
                            now: datetime) -> PlayoffPlannerData:  # fmt: skip
    """Everything the module publishes (module docstring); the warehouse is opened read-only
    (the season's unit points only)."""
    from twm import pins
    from twm.modules.playoff_planner import production as pr
    from twm.modules.playoff_planner import weekly as wk

    try:
        spec, pin = pr.load_pinned(season)
    except pins.PinError as e:
        raise PublishInputError(f"the playoff planner's spec cannot be read: {e}") from e
    lists, rows = snapshot_frames(wk.read_snapshots(store))
    actuals = wk.read_actuals(warehouse, int(season)).select(
        pl.col(c).cast(t) for c, t in ACTUALS.items())  # fmt: skip
    approved = datetime.fromisoformat(pin.approved).replace(tzinfo=UTC) if pin.approved else now
    weeks = tuple(int(w) for w in spec.content["playoff_weeks"])
    d = PlayoffPlannerData(season=int(season), version=spec.model_version, lists=lists,
                           rows=rows, tables=pin_tables(spec.content),
                           versions=version_row(spec.content, approved.astimezone(UTC)),
                           actuals=actuals, weeks=weeks)  # fmt: skip
    # the local store's record; the publish regrades it with the target's snapshots (a fresh
    # runner's store holds only the night's): write.publish, live_record
    d.tables["playoff_planner_live"] = live_record(d, no_published())
    return d


def meta(d: PlayoffPlannerData) -> dict[str, str]:
    """site_meta's ``playoff_planner_season`` (the pinned season) and ``playoff_planner_weeks``
    (its fantasy playoff weeks, '15,16,17'): the page's empty state names them."""
    return {"playoff_planner_season": str(d.season),
            "playoff_planner_weeks": ",".join(str(w) for w in d.weeks)}  # fmt: skip


def problems(d: PlayoffPlannerData, teams: set[str], versions: set[str]) -> list[str]:
    """Plain-English problems of the module's rows (empty = publishable)."""
    from twm.publish.collect import _team_problems

    out: list[str] = []
    rows, key = d.rows, list(PLAYOFF_PLANNER.key)
    if rows.select(TABLES[PLAYOFF_PLANNER.rows].key).is_duplicated().any():
        out.append("a playoff-planner snapshot lists a (week, team, position) twice")
    sizes = rows.group_by(key).agg(pl.len().cast(pl.Int32).alias("_n"))
    if d.lists.join(sizes, on=key, how="full", coalesce=True).filter(
            pl.col("_n").is_null() | (pl.col("_n") != pl.col("n_rows"))).height:  # fmt: skip
        out.append("a playoff-planner snapshot's row count differs from its rows")
    if rows.filter(~pl.col("position").is_in(POSITIONS)).height:
        out.append("some playoff-planner rows are not a QB, RB, WR, TE, K or DST")
    if rows.filter(~pl.col("candidate").is_in(CANDIDATES)).height:
        out.append("some playoff-planner rows name an unknown candidate")
    bye, rating = pl.col("opponent").is_null(), pl.col("rating")
    if rows.filter(bye != rating.is_null()).height:
        out.append("some playoff-planner rows have a rating without an opponent or the reverse")
    if rows.filter(rating.is_nan() | (rating <= 0)).height:
        out.append("some playoff-planner ratings are not positive numbers")
    none = pl.col("candidate") == "none"
    if rows.filter(none & (pl.col("rating_rank").is_not_null() | (rating != 1.0))).height:
        out.append("some unrated (candidate 'none') playoff-planner rows carry a rating or rank")
    rank = pl.col("rating_rank")
    if rows.filter(rank.is_not_null() & ((rank < 1) | (rank > 32))).height:
        out.append("some playoff-planner ranks are outside 1-32")
    out += _team_problems("playoff-planner rows", rows, teams)
    out += _team_problems("playoff-planner rows", rows, teams, "opponent", required=False)
    unknown = set(d.lists.get_column("model_version").to_list()) - versions
    if unknown:
        out.append(f"playoff-planner snapshots of unknown model versions: {sorted(unknown)[:3]}")
    return out
