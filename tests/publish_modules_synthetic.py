"""Small made-up streamer, Regression Watch and (step H4a) Hot-Seat lists for `twm publish`
tests (step P2), added to the Waiver Radar's synthetic publish data (tests/publish_synthetic.py):
backtest lists of two seasons and one live week, their outcomes, model versions and track
records. Deterministic."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from tests.publish_synthetic import TEAMS
from twm.modules.regression_watch.weekly import early_note
from twm.publish.collect import ListData, PublishData
from twm.publish.hot_seat_lists import LIST_SCHEMA as HS_LIST
from twm.publish.hot_seat_lists import OUTCOME_SCHEMA as HS_OUT
from twm.publish.hot_seat_lists import ROW_SCHEMA as HS_ROW
from twm.publish.regression_lists import LIST_SCHEMA as RW_LIST
from twm.publish.regression_lists import OUTCOME_SCHEMA as RW_OUT
from twm.publish.regression_lists import ROW_SCHEMA as RW_ROW
from twm.publish.stream_lists import LIST_SCHEMA as ST_LIST
from twm.publish.stream_lists import OUTCOME_SCHEMA as ST_OUT
from twm.publish.stream_lists import PICK_SCHEMA as ST_PICK
from twm.publish.tables import TABLES

CREATED = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
LIVE_SEASON, LIVE_WEEK = 2026, 3
KICKERS = [f"00-009{i:04d}" for i in range(5)]
PY_TYPES = {"text": pl.String, "integer": pl.Int32, "double precision": pl.Float64}


def _version(version: str, module: str, model: str, label: str, season: int) -> dict:
    return {"model_version": version, "module": module, "model": model, "label": label,
            "training_seasons": list(range(2013, season)), "test_season": season,
            "feature_list": ["x"], "params": "{}", "created_at": CREATED}  # fmt: skip


def _table(name: str, rows: list[dict]) -> pl.DataFrame:
    t = TABLES[name]
    return pl.DataFrame(rows, schema={c: PY_TYPES[ty] for c, ty in t.columns}, orient="row")


def streamer(
    *, backtest: tuple[int, ...] = (2024, 2025), live: bool = True, shift: int = 0,
    incomplete: bool = False, n_track: int = 3,
) -> tuple[ListData, list[dict], pl.DataFrame]:  # fmt: skip
    """(lists, versions, track record) of the streamer; ``shift`` rotates the live K order."""
    weeks = [(s, w, "backtest") for s in backtest for w in (1, 2)]
    weeks += [(LIVE_SEASON, LIVE_WEEK, "live")] if live else []
    lists, picks, versions = [], [], {}
    for s, w, kind in weeks:
        for pos in ("K", "DST"):
            ids = KICKERS if pos == "K" else [f"DST-{t}" for t in TEAMS]
            if kind == "live" and pos == "K":
                ids = ids[shift:] + ids[:shift]
            model = "logit_k" if pos == "K" else "baseline_opponent_dst"
            version = f"{model}-{'live' if kind == 'live' else s}"
            versions[version] = _version(version, "streamer", model, "y_start", s)
            lists.append({"season": s, "week": w, "position": pos, "kind": kind,
                          "as_of": datetime(s, 9, 9, 14, tzinfo=UTC), "model_version": version,
                          "generated_at": CREATED, "incomplete": incomplete and kind == "live",
                          "n_pool": len(ids), "note": None})  # fmt: skip
            for r, e in enumerate(ids, start=1):
                team = TEAMS[KICKERS.index(e) % 4] if pos == "K" else e[4:]
                chance = 0.5 - 0.05 * r
                picks.append({
                    "season": s, "week": w, "position": pos, "kind": kind, "rank": r,
                    "entity_id": e, "entity_type": "kicker" if pos == "K" else "team_defense",
                    "display_name": f"{e} name", "team": team,
                    "next_opponent": TEAMS[(TEAMS.index(team) + 1) % 4], "home": r % 2 == 0,
                    "chance": chance, "chance_low": chance - 0.05, "chance_high": chance + 0.05,
                    "model_prob": 0.4 if pos == "K" else None, "tier": "speculative",
                    "reasons": ["a reason"] if kind == "live" else [],
                })  # fmt: skip
    lists_df = pl.DataFrame(lists, schema=ST_LIST, orient="row")
    picks_df = pl.DataFrame(picks, schema=ST_PICK, orient="row")
    keys = picks_df.select("season", "week", "entity_id").unique(maintain_order=True)
    out = keys.join(picks_df.select("season", "week", "entity_id", "rank"),
                    on=["season", "week", "entity_id"]).select(  # fmt: skip
        "season", "week", "entity_id", (pl.col("rank") == 1).alias("y_start"),
        pl.when(pl.col("season") < LIVE_SEASON).then(pl.lit("final")).otherwise(
            pl.lit("pending")).alias("label_status"),
        (10.0 - pl.col("rank")).cast(pl.Float64).alias("points_next_week"),
    ).unique(["season", "week", "entity_id"], maintain_order=True).cast(ST_OUT)  # fmt: skip
    track = _table("stream_track_record", [
        {"line": i + 1, "position": "K", "method": "logit", "train_on": "pool", "scope": "season",
         "seasons": str(2013 + i), "key": None, "metric": "p_at_5", "value": 0.4, "lo": None,
         "hi": None, "share_above_zero": None, "n_groups": 16, "n_rows": 180, "n_pos": 40}
        for i in range(n_track)])  # fmt: skip
    return ListData(lists_df, picks_df, out, keys), list(versions.values()), track


def regression(
    players: pl.DataFrame, *, backtest: tuple[int, ...] = (2025,), live: bool = True,
    shift: int = 0, incomplete: bool = False, n_track: int = 3,
) -> tuple[ListData, list[dict], pl.DataFrame]:  # fmt: skip
    """(lists, versions, track record) of Regression Watch over six of ``players`` (dim_player
    rows); ``shift`` changes the live list's projections."""
    who = players.filter(pl.col("position").is_in(["QB", "RB", "WR", "TE"])).head(6)
    weeks = [(s, w, "backtest") for s in backtest for w in (4, 6)]
    weeks += [(LIVE_SEASON, LIVE_WEEK, "live")] if live else []
    # the product's tags only (Legit, stripped by the publish since R1, never reaches the rows)
    tagging = [("sell_high", ["sell_high"]), ("buy_low", ["buy_low"]), (None, []), (None, []),
               (None, []), (None, [])]  # fmt: skip
    lists, rows, versions = [], [], {}
    for s, w, kind in weeks:
        version = f"mean_flat_all-{'live' if kind == 'live' else s}"
        versions[version] = _version(version, "regression_watch", "mean_flat_all", "ppg_ros", s)
        lists.append({"season": s, "week": w, "kind": kind,
                      "as_of": datetime(s, 9, 9, 14, tzinfo=UTC), "params_version": version,
                      "generated_at": CREATED, "incomplete": incomplete and kind == "live",
                      "n_universe": who.height, "note": early_note(w)})  # fmt: skip
        for i, (gsis, pos) in enumerate(who.select("gsis_id", "position").iter_rows()):
            tag, tags = tagging[i]
            ppg = 10.0 + i + (shift if kind == "live" else 0)
            rows.append({
                "season": s, "week": w, "kind": kind, "gsis_id": gsis, "position": pos,
                "team": TEAMS[i % 4], "games": 3, "ppg": ppg, "ppg_ng": ppg - 0.5,
                "xfp_pg": 9.0, "xfp_pg_ng": 8.5, "fpoe_pg": ppg - 9.0, "fpoe_pg_ng": ppg - 9.0,
                "projection": 9.5 + 0.1 * i, "shrinkage": 0.1, "tag": tag, "tags": tags,
                "tag_reason": f"{tag} because" if tag else None,
                # the 80% range (feature #4): the backtest lists' only (a live list scored
                # before the feature has none)
                "projection_lo": None if kind == "live" else 6.5 + 0.1 * i,
                "projection_hi": None if kind == "live" else 13.0 + 0.1 * i,
            })  # fmt: skip
    lists_df = pl.DataFrame(lists, schema=RW_LIST, orient="row")
    rows_df = pl.DataFrame(rows, schema=RW_ROW, orient="row")
    keys = rows_df.select("season", "week", "gsis_id")
    out = keys.with_columns(
        pl.when(pl.col("season") < LIVE_SEASON).then(12.0).alias("ros_ppg"),
        pl.when(pl.col("season") < LIVE_SEASON).then(10).alias("ros_games"),
        pl.when(pl.col("season") < LIVE_SEASON).then(pl.lit("final")).otherwise(
            pl.lit("pending")).alias("label_status"),
    ).cast(RW_OUT)  # fmt: skip
    track = _table("regression_track_record", [
        {"line": i + 1, "section": "choice", "weeks": None, "position": None,
         "method": "mean_hl8_all", "metric": "validation_mae", "row_group": None,
         "season": 2011 + i, "value": 3.1, "lo": None, "hi": None, "n": 674, "n_seasons": None,
         "share_above_zero": None, "per_asof": None, "not_graded": None, "detail": "x"}
        for i in range(n_track)])  # fmt: skip
    return ListData(lists_df, rows_df, out, keys), list(versions.values()), track


def stability(n: int = 4) -> pl.DataFrame:
    """``n`` rows of Regression Watch's stability study (regression_stability, step R1): a
    split-half row, then shrinkage rows r(g) for g = 1, 2, ..."""
    rows = [{"line": 1, "section": "split_half", "seasons": "2009-2025", "split": "odd_even",
             "position": "QB", "metric": "xfp", "g": None, "n": 607, "value": 0.76435,
             "lo": 0.721062, "hi": 0.799518, "var_signal": None, "var_noise": None,
             "prior_mean": None}]  # fmt: skip
    rows += [{"line": i + 2, "section": "shrinkage", "seasons": "2009-2025", "split": "odd_even",
              "position": "QB", "metric": "fpoe", "g": i + 1, "n": 607, "value": 0.0166 * (i + 1),
              "lo": None, "hi": None, "var_signal": 0.588076, "var_noise": 34.904076,
              "prior_mean": -0.784906} for i in range(n - 1)]  # fmt: skip
    return _table("regression_stability", rows[:n])


def add_modules(data: PublishData, **kw) -> PublishData:
    """``data`` (the Radar's synthetic publish) with the streamer's, Regression Watch's, the
    Hot-Seat Meter's and the board's lists; ``streamer=`` / ``regression=`` / ``stability=`` /
    ``hot_seat=`` / ``board=`` pass keyword arguments to each builder (``hot_seat=None`` /
    ``board=None``: left out)."""
    st, st_versions, st_track = streamer(**kw.get("streamer", {}))
    rw, rw_versions, rw_track = regression(data.tables["dim_player"], **kw.get("regression", {}))
    data.families["streamer"], data.families["regression_watch"] = st, rw
    data.tables["stream_track_record"], data.tables["regression_track_record"] = st_track, rw_track
    data.tables["regression_stability"] = stability(**kw.get("stability", {}))
    hs_versions: list[dict] = []
    if kw.get("hot_seat", {}) is not None:
        hs, hs_versions, hs_track, hs_tables = hot_seat(**kw.get("hot_seat", {}))
        data.families["hot_seat"], data.tables["hot_seat_track_record"] = hs, hs_track
        data.tables["hot_seat_firings"] = hs_tables["hot_seat_firings"]
        old = data.tables.get("dim_coach")
        both = [old, hs_tables["dim_coach"]] if old is not None else [hs_tables["dim_coach"]]
        data.tables["dim_coach"] = pl.concat(both).unique("coach_id").sort("coach_id")
    bd_versions: list[dict] = []
    if kw.get("board", {}) is not None:  # step I2c-a
        bd, bd_versions, bd_track, bd_tables = board(data.tables["dim_player"],
                                                     **kw.get("board", {}))  # fmt: skip
        data.families["board"], data.tables["board_track_record"] = bd, bd_track
        data.tables.update(bd_tables)
    mv = data.tables["model_versions"]
    extra = pl.DataFrame([*st_versions, *rw_versions, *hs_versions, *bd_versions],
                         schema=mv.schema, orient="row")  # fmt: skip
    data.tables["model_versions"] = pl.concat([mv, extra]).unique(
        "model_version", keep="first", maintain_order=True)  # fmt: skip
    return data


HS_COACHES = [("coach-a", "Coach A"), ("coach-b", "Coach B"), ("coach-c", "Coach C"),
              ("coach-d", "Coach D")]  # fmt: skip


def hot_seat(
    *, backtest: tuple[int, ...] = (2025,), live: bool = True, shift: float = 0.0,
    incomplete: bool = False, n_track: int = 3,
) -> tuple[ListData, list[dict], pl.DataFrame, dict[str, pl.DataFrame]]:  # fmt: skip
    """(lists, versions, track record, {hot_seat_firings, dim_coach}) of the Hot-Seat Meter
    over four coaches: weekly week 3 and the end-of-season snapshot (week 18) of each backtest
    season, the live week 3; ``shift`` raises the live list's first probability."""
    weeks = [(s, w, snap, "backtest") for s in backtest
             for w, snap in ((3, "weekly"), (18, "end_of_season"))]  # fmt: skip
    weeks += [(LIVE_SEASON, LIVE_WEEK, "weekly", "live")] if live else []
    lists, rows, versions = [], [], {}
    for s, w, snap, kind in weeks:
        version = f"logit-hs{'live' if kind == 'live' else s}"
        versions[version] = _version(version, "hot_seat", "logit", "y", s)
        lists.append({"season": s, "week": w, "snapshot": snap, "kind": kind,
                      "as_of": datetime(s, 9, 22, 14, tzinfo=UTC), "model_version": version,
                      "generated_at": CREATED, "incomplete": incomplete and kind == "live",
                      "n_coaches": len(HS_COACHES), "note": None})  # fmt: skip
        for i, (coach, _) in enumerate(HS_COACHES):
            p = 0.6 - 0.1 * i + (shift if kind == "live" and i == 0 else 0.0)
            rows.append({
                "season": s, "week": w, "snapshot": snap, "kind": kind, "coach_id": coach,
                "team": TEAMS[i % 4], "as_of": datetime(s, 9, 22, 14, tzinfo=UTC), "rank": i + 1,
                "probability": p, "is_interim": i == 3,
                "drivers": '[{"feature": "wins_vs_expected", "label": "Wins vs market '
                           'expectation", "contribution": 0.5, "value": -1.0, "missing": false}]',
                "reg_games_played": 3, "reg_wins": 1.0, "expected_wins": 1.5,
                "wins_vs_expected": -0.5, "point_diff_per_game": -3.0, "tenure_seasons": 2,
                "division_rank": 3, "prev_season_wins": 7.0, "consecutive_losing_seasons": 1,
                "fourth_down_wp_lost_per_game": 0.01,
            })  # fmt: skip
    lists_df = pl.DataFrame(lists, schema=HS_LIST, orient="row")
    rows_df = pl.DataFrame(rows, schema=HS_ROW, orient="row")
    keys = rows_df.select("season", "week", "coach_id").unique()
    done = pl.col("season") < LIVE_SEASON
    out = keys.with_columns(
        pl.when(done).then(pl.col("coach_id") == "coach-a").alias("departed"),
        pl.when(done).then(False).alias("censored"),
        pl.when(done & (pl.col("coach_id") == "coach-a")).then(pl.lit("fired_after_season"))
        .alias("departure_type"), pl.lit(None, dtype=pl.Date).alias("announced"),
        pl.when(done).then(pl.lit("final")).otherwise(pl.lit("pending")).alias("label_status"),
    ).cast(HS_OUT).sort("season", "week", "coach_id")  # fmt: skip
    track = _table("hot_seat_track_record", [
        {"line": i + 1, "variant": "main", "model": "logit", "prob": "prob",
         "slice": f"week_{i + 2:02d}", "metric": "roc_auc", "value": 0.78, "lo": 0.7,
         "hi": 0.85, "n_rows": 600, "n_pos": 100, "n_seasons": 20}
        for i in range(n_track)])  # fmt: skip
    firings = _table("hot_seat_firings", [
        {"season": s, "positive_departures": 6, "fired_in_season": 2, "positives_week_12": 5,
         "positives_end_of_season": 6, "censored_coach_seasons": 1, "interim_coach_seasons": 2}
        for s in (2024, 2025)])  # fmt: skip
    coaches = pl.DataFrame(HS_COACHES, schema={"coach_id": pl.String, "name": pl.String},
                           orient="row")  # fmt: skip
    return (ListData(lists_df, rows_df, out, keys), list(versions.values()), track,
            {"hot_seat_firings": firings, "dim_coach": coaches})  # fmt: skip


def board(
    players: pl.DataFrame, *, backtest: tuple[int, ...] = (2025,), live: bool = True,
    shift: float = 0.0, n_track: int = 3,
) -> tuple[ListData, list[dict], pl.DataFrame, dict[str, pl.DataFrame]]:  # fmt: skip
    """(lists, versions, track record, {board_disagreement}) of the Cliff board (step I2c-a)
    over four of ``players``: one board (week 0, 'preseason') per backtest season and the live
    season's; ``shift`` raises the live board's first Cliff chance."""
    from twm.publish import board_lists as pb

    who = players.filter(pl.col("position").is_in(["QB", "RB", "WR", "TE"])).head(4)
    boards = [(s, "backtest") for s in backtest] + ([(LIVE_SEASON, "live")] if live else [])
    lists, rows, versions = [], [], {}
    for s, kind in boards:
        tag = "live" if kind == "live" else str(s)
        cv, mv = f"logit-bd{tag}", f"logit_simple-bd{tag}"
        versions[cv] = _version(cv, "board", "logit", "y_cliff", s - 1)
        versions[mv] = _version(mv, "board", "logit_simple", "y_missed", s - 1)
        lists.append({"season": s, "week": 0, "snapshot": "preseason", "kind": kind,
                      "as_of": datetime(s, 9, 9, 23, tzinfo=UTC), "model_version": cv,
                      "missed_version": mv, "generated_at": CREATED, "incomplete": False,
                      "n_players": who.height, "note": None})  # fmt: skip
        for i, (gsis, pos) in enumerate(who.select("gsis_id", "position").iter_rows()):
            p = 0.6 - 0.1 * i + (shift if kind == "live" and i == 0 else 0.0)
            drv = '[{"feature": "age", "label": "Age", "contribution": 0.4, "value": 30.0, ' \
                  '"missing": false}]'  # fmt: skip
            rows.append({
                "season": s, "week": 0, "snapshot": "preseason", "kind": kind, "gsis_id": gsis,
                "team": TEAMS[i % 4], "position": pos, "as_of": datetime(s, 9, 9, 23, tzinfo=UTC),
                "cliff_rank": i + 1, "cliff_probability": p, "missed_rank": 4 - i,
                "missed_probability": 0.1 + 0.05 * i, "ecr_rank": None if i == 3 else 5 + i,
                "age": 28.0 + i, "prior_seasons": 4 + i, "games_s": 15, "ppg_s": 14.0 - i,
                "pos_rank_s": 6 + i, "ppg_change": 1.5, "touches_per_game_s": 12.0,
                "depth_rank_s1": 1, "team_change_s1": False, "dc_absent": False,
                "hc_change_s1": i == 2, "cliff_drivers": drv, "missed_drivers": drv,
            })  # fmt: skip
    lists_df = pl.DataFrame(lists, schema=pb.LIST_SCHEMA, orient="row")
    rows_df = pl.DataFrame(rows, schema=pb.ROW_SCHEMA, orient="row")
    keys = rows_df.select("season", "week", "gsis_id").unique()
    done = pl.col("season") < LIVE_SEASON
    out = keys.with_columns(
        pl.when(done).then(14).alias("games_s1"), pl.when(done).then(9.0).alias("ppg_s1"),
        pl.when(done).then(pl.col("gsis_id") == who["gsis_id"][0]).alias("y_cliff"),
        pl.when(done).then(False).alias("y_missed"),
        pl.when(done).then(pl.lit("final")).otherwise(pl.lit("pending")).alias("label_status"),
    ).select(list(pb.OUTCOME_SCHEMA)).cast(pb.OUTCOME_SCHEMA).sort("season", "gsis_id")  # fmt: skip
    track = pl.DataFrame([
        {"line": i + 1, "population": "cliff", "research": False, "variant": "cliff_main",
         "slice": "all", "model": "logit", "vs": None, "metric": "pr_auc", "value": 0.4,
         "lo": 0.35, "hi": 0.46, "share_above_zero": None} for i in range(n_track)],
        schema={"line": pl.Int32, "population": pl.String, "research": pl.Boolean,
                "variant": pl.String, "slice": pl.String, "model": pl.String, "vs": pl.String,
                "metric": pl.String, "value": pl.Float64, "lo": pl.Float64, "hi": pl.Float64,
                "share_above_zero": pl.Float64}, orient="row")  # fmt: skip
    dis = pl.DataFrame(
        [{"variant": "cliff_main", "model": "logit", "season": 2025, "pick_group": g,
          "players": 3, "hits": 1} for g in ("both", "ecr_only", "model_only")],
        schema={"variant": pl.String, "model": pl.String, "season": pl.Int32,
                "pick_group": pl.String, "players": pl.Int32, "hits": pl.Int32},
        orient="row")  # fmt: skip
    return (ListData(lists_df, rows_df, out, keys), list(versions.values()), track,
            {"board_disagreement": dis})  # fmt: skip
