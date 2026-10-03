"""Regression Watch's frozen backtest lists (step P2, the reviewer's follow-up): the time
machine's lists, built ONCE on the owner's Mac from the full warehouse, committed as a zstd
Parquet snapshot pinned with its sha256 (like the Radar's and the streamer's backtests), and
read by the publish on every run. The scheduled job never recomputes them: a Linux rebuild
could flip a near-tie of D3's variant choice (validation MAEs 0.0004 apart), and it would need
the warehouse from 2006.

**What it holds** (``artifacts/production_models/regression_watch/backtest-<params version>/``):

- ``predictions``: every universe player of the headline as-of weeks (4, 6, 8, 10) of every D3
  test season (2011 .. the season before the approved one), made by the weekly list's own code
  (:func:`~twm.modules.regression_watch.weekly.score_week`) with the parameters D3's
  walk-forward chose for that season (:func:`~twm.modules.regression_watch.production.
  make_params`: variant and X chosen on the seasons before, shrinkage estimated on 2009 ..
  S-1), in the store's layout (score = projection, band = tag, reasons_json = the numbers) plus
  the player's ``team`` (his latest game public at the as-of). The lists carry all three D3
  tags, Legit too (the backtest as it was run); the product dropped Legit on 2026-09-30 and
  ``twm publish`` strips it (:mod:`twm.publish.regression_lists`);
- ``outcomes``: each row's rest of the season after the as-of: ``ros_ppg`` (NULL with fewer
  than 3 games, as D3 grades), ``ros_games`` and ``ros_rank`` (his rest-of-season PPG rank at
  his position), all 'final';
- ``model_versions``: one row per season, its parameters and, in ``notes``, the choice (variant,
  validation MAE, X and their precision).

**Checked** (:func:`snapshot_mismatches`, by ``twm model check`` and before approving): every
season's choice equals ``reports/regression_watch/backtest.csv`` (choice and threshold rows),
the rows reproduce its headline MAE of the projection per position and pooled and its tag hit
rates (n, rate, not graded), and the approved parameters' record (the choice for the last
backtest season) equals the snapshot's.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from twm.config import League

TABLES = ("predictions", "outcomes", "model_versions")
PREDICTION_COLUMNS = ("season", "week", "as_of", "entity_id", "rank_group", "rank", "score",
                      "band", "model_version", "reasons_json", "horizon", "team")  # fmt: skip
OUTCOME_COLUMNS = ("season", "week", "entity_id", "ros_ppg", "ros_games", "ros_rank",
                   "label_status")  # fmt: skip
VERSION_COLUMNS = ("model_version", "module", "model", "label", "feature_list", "params",
                   "training_seasons", "test_season", "notes")  # fmt: skip
TOLERANCE = 1.01e-6  # the backtest CSV prints 6 decimals
Progress = Callable[[str], None]


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def no_negative_zero(df: pl.DataFrame) -> pl.DataFrame:
    """Every float -0.0 as 0.0: the D1 frame's sums land on either zero from run to run."""
    cols = [c for c, d in df.schema.items() if d == pl.Float64]
    return df.with_columns(pl.when(pl.col(c) == 0).then(pl.lit(0.0)).otherwise(pl.col(c))
                           .alias(c) for c in cols)  # fmt: skip


def teams_at(frame: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    """(season, week, gsis_id, team) for every (season, week, as_of) of ``keys``: the team of
    each player's latest game public at that as-of (as the list's own player_state has it)."""
    from twm.modules.regression_watch.player_week import visible

    parts = []
    for s, w, as_of in keys.select("season", "week", "as_of").unique().sort("season", "week") \
            .iter_rows():  # fmt: skip
        seen = visible(frame.filter(pl.col("season") == s), _aware(as_of))
        parts.append(
            seen.sort("gsis_id", "week")
            .group_by("gsis_id")
            .agg(pl.col("team").last())
            .with_columns(
                pl.lit(s, dtype=pl.Int32).alias("season"), pl.lit(w, dtype=pl.Int32).alias("week")
            )  # fmt: skip
        )
    if not parts:
        return pl.DataFrame(schema={"gsis_id": pl.String, "team": pl.String,
                                    "season": pl.Int32, "week": pl.Int32})  # fmt: skip
    return pl.concat(parts, how="vertical")


def rest_of_season_at(frame: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    """(season, week, gsis_id, ros_ppg, ros_games, ros_rank) of every player with a game public
    at each (season, week, as_of) of ``keys``, over ``frame``'s rows after it (D3's
    :func:`~twm.modules.regression_watch.backtest.rest_of_season`); ros_ppg NULL with fewer
    than 3 games (D3 does not grade those)."""
    from twm.modules.regression_watch import backtest as bt
    from twm.modules.regression_watch import projection as pj
    from twm.modules.regression_watch.player_week import visible

    parts = []
    for s, w, as_of in keys.select("season", "week", "as_of").unique().sort("season", "week") \
            .iter_rows():  # fmt: skip
        sf = frame.filter(pl.col("season") == s)
        players = visible(sf, _aware(as_of)).select("gsis_id").unique()
        ros = bt.rest_of_season(sf, _aware(as_of)).select("gsis_id", "ros_games", "ros_ppg",
                                                            "ros_rank")  # fmt: skip
        parts.append(players.join(ros, on="gsis_id", how="left").select(
            pl.lit(s, dtype=pl.Int32).alias("season"), pl.lit(w, dtype=pl.Int32).alias("week"),
            "gsis_id",
            pl.when(pl.col("ros_games").fill_null(0) >= pj.MIN_GAMES).then(pl.col("ros_ppg"))
            .cast(pl.Float64).alias("ros_ppg"),
            pl.col("ros_games").fill_null(0).cast(pl.Int32), pl.col("ros_rank").cast(pl.Int32),
        ))  # fmt: skip
    schema = {"season": pl.Int32, "week": pl.Int32, "gsis_id": pl.String, "ros_ppg": pl.Float64,
              "ros_games": pl.Int32, "ros_rank": pl.Int32}  # fmt: skip
    return pl.concat(parts, how="vertical") if parts else pl.DataFrame(schema=schema)


def build_snapshot(
    db: Path | str, season: int, league: League, *, xfp_source: str,
    progress: Progress | None = None, xfp_dir: Path | None = None,
) -> dict[str, pl.DataFrame]:  # fmt: skip
    """The snapshot of the approved ``season`` (module docstring) from the warehouse, which must
    reach back to 2006 (the owner's Mac; never the scheduled job), with the xFP of
    ``xfp_source`` (own: each season's walk-forward fold, its folds in ``xfp_dir`` when given:
    the Time Machine check's scratch copy). Deterministic."""
    from twm.modules.regression_watch import backtest as bt
    from twm.modules.regression_watch import production as rprod
    from twm.modules.regression_watch import projection as pj
    from twm.modules.regression_watch import weekly as rwk
    from twm.modules.regression_watch import xfp_source as xs
    from twm.modules.regression_watch.player_week import player_games_history, visible
    from twm.modules.waiver_radar.weekly import last_reg_week

    last = int(season) - 1
    xfp = xs.history(db, last, xfp_source, progress=progress, out_dir=xfp_dir)
    frame = player_games_history(db, list(range(pj.FIRST_DATA_SEASON, last + 1)), xfp=xfp)
    if pj.FIRST_DATA_SEASON not in set(frame.get_column("season").unique().to_list()):
        raise rprod.RegressionProductionError(
            f"the warehouse has no {pj.FIRST_DATA_SEASON} games: build it from 2006 first"
        )
    asofs = bt.asof_table(db, list(range(pj.FIRST_DATA_SEASON, last + 1)), bt.HEADLINE_WEEKS)
    rows, used = bt.build_rows(frame, asofs, league, range(bt.FIRST_VALIDATION_SEASON, last + 1),
                               progress=progress)  # fmt: skip
    priors = {p.season: p.priors for p in used}
    tests = list(range(bt.FIRST_TEST_SEASON, last + 1))
    choices = {s: bt.choose(rows, s) for s in tests}
    names = pl.DataFrame(schema={"gsis_id": pl.String, "name": pl.String})
    lists, versions = [], []
    for s in tests:
        record = choices[s - 1] if s - 1 in choices else choices[s]
        params = rprod.make_params(s, choices[s], record, priors[s], xfp_source)
        versions.append(rprod.version_frame(params))
        sf, end = frame.filter(pl.col("season") == s), last_reg_week(db, s) or 0
        for week, as_of in asofs.filter(pl.col("season") == s).select("week", "as_of").iter_rows():
            # all three tags of the D3 backtest (Legit too): the snapshot is history, rebuilt
            # byte for byte; the publish strips Legit (owner, 2026-09-30)
            table = rwk.score_week(visible(sf, _aware(as_of)), names, params, league,
                                   int(week), tags=rwk.TESTED_TAGS)  # fmt: skip
            if table.height:
                lists.append(rwk.list_rows(table, params, as_of=_aware(as_of), horizon=end - week,
                                           kind="backtest", created=datetime(2000, 1, 1),
                                           incomplete=False))  # fmt: skip
    preds = pl.concat(lists, how="vertical")
    keys = preds.select("season", "week", "as_of").unique()
    teams = teams_at(frame, keys).rename({"gsis_id": "entity_id"})
    preds = preds.join(teams, on=["season", "week", "entity_id"], how="left")
    ros = rest_of_season_at(frame, keys).rename({"gsis_id": "entity_id"})
    outs = (
        preds.select("season", "week", "entity_id")
        .join(ros, on=["season", "week", "entity_id"], how="left")
        .with_columns(pl.lit("final").alias("label_status"))
    )
    return {
        "predictions": no_negative_zero(
            preds.select(PREDICTION_COLUMNS).sort("season", "week", "rank_group", "rank")
        ),  # fmt: skip
        "outcomes": no_negative_zero(
            outs.select(OUTCOME_COLUMNS).sort("season", "week", "entity_id")
        ),  # fmt: skip
        "model_versions": pl.concat(versions).select(VERSION_COLUMNS).sort("test_season"),
    }


def choices_of(frames: dict[str, pl.DataFrame]) -> dict[int, dict[str, Any]]:
    """Season -> the choice D3 made for it (variant, validation MAE, X), from the versions."""
    v = frames["model_versions"]
    pairs = v.select("test_season", "notes").iter_rows()
    return {int(s): json.loads(n)["choice"] for s, n in pairs}


def _csv_problems(frames: dict[str, pl.DataFrame], ev: pl.DataFrame, league: League) -> list[str]:
    """Where the snapshot disagrees with the committed backtest CSV (module docstring)."""
    out = []

    def near(a: Any, b: Any) -> bool:
        return a is not None and b not in (None, "") and abs(float(a) - float(b)) <= TOLERANCE

    for s, ch in sorted(choices_of(frames).items()):
        c = ev.filter((pl.col("table") == "choice") & (pl.col("season") == str(s)))
        th = {r["metric"]: r["value"] for r in ev.filter(
            (pl.col("table") == "threshold") & (pl.col("season") == str(s))
            & (pl.col("group") == "chosen")).iter_rows(named=True)}  # fmt: skip
        if c.height != 1 or c["method"][0] != ch["variant"] or not near(
                ch["validation_mae"], c["value"][0]):  # fmt: skip
            out.append(f"{s}: choice {ch['variant']} ({ch['validation_mae']}) is not the report's")
        for tag in ("sell_high", "buy_low"):
            if not near(ch[tag]["x"], th.get(tag)):
                out.append(f"{s}: {tag} X {ch[tag]['x']} is not the report's {th.get(tag)}")
    p = frames["predictions"].join(frames["outcomes"], on=["season", "week", "entity_id"])
    recs = [json.loads(r) for r in p.get_column("reasons_json").to_list()]
    starters = league.starter_thresholds()
    p = p.with_columns(
        pl.Series("ppg", [r["ppg"] for r in recs], dtype=pl.Float64),
        pl.Series("tags", [r["tags"] for r in recs], dtype=pl.List(pl.String)),
        (pl.col("ros_games") >= 3).alias("graded"),
        pl.col("rank_group")
        .replace_strict(starters, default=0, return_dtype=pl.Int64)
        .alias("_st"),
    )
    hits = {"sell_high": pl.col("ros_ppg") < pl.col("ppg"),
            "buy_low": pl.col("ros_ppg") > pl.col("ppg"),
            "legit": pl.col("ros_rank") <= pl.col("_st")}  # fmt: skip
    rows = ev.filter(pl.col("weeks") == "headline")
    for pos in ("QB", "RB", "WR", "TE", "all"):
        sub = p if pos == "all" else p.filter(pl.col("rank_group") == pos)
        g = sub.filter(pl.col("graded"))
        want = rows.filter(
            (pl.col("table") == "value") & (pl.col("position") == pos)
            & (pl.col("method") == "model") & (pl.col("metric") == "mae")
        )  # fmt: skip
        mae = (g["score"] - g["ros_ppg"]).abs().mean()
        if want.height != 1 or not near(mae, want["value"][0]) or str(g.height) != want["n"][0]:
            out.append(f"{pos} headline MAE {mae} over {g.height} rows is not the report's")
        for tag, hit in hits.items():
            tagged = sub.filter(pl.col("tags").list.contains(tag))
            graded = tagged.filter(pl.col("graded"))
            rate = graded.select(hit.cast(pl.Float64).mean()).item()
            w = rows.filter(
                (pl.col("table") == "tag") & (pl.col("position") == pos)
                & (pl.col("metric") == tag) & (pl.col("group") == "tagged")
            )  # fmt: skip
            ok = w.height == 1 and near(rate, w["value"][0]) and str(graded.height) == w["n"][0]
            if not ok or str(tagged.height - graded.height) != (w["not_graded"][0] or "0"):
                out.append(f"{pos} {tag}: {graded.height} graded at {rate} is not the report's")
    return out


def snapshot_mismatches(
    frames: dict[str, pl.DataFrame], csv_path: Path, league: League, params: Any = None
) -> list[str]:
    """Where the snapshot disagrees with ``csv_path`` or with the approved ``params`` (whose
    record is the choice for the last backtest season) ([] = consistent)."""
    if not csv_path.exists():
        return [f"the backtest report is missing: {csv_path}"]
    out = _csv_problems(frames, pl.read_csv(csv_path, infer_schema_length=0), league)
    versions = dict(frames["model_versions"].select("test_season", "model_version").iter_rows())
    seasons = frames["predictions"].select("season", "model_version").unique()
    wrong = [s for s, v in seasons.iter_rows() if versions.get(s) != v]
    if wrong:
        out.append(f"lists of {sorted(set(wrong))[:3]} name another season's parameters")
    if params is not None:
        rec = choices_of(frames).get(int(params.season) - 1)
        if json.dumps(rec, sort_keys=True) != json.dumps(params.record, sort_keys=True):
            out.append(f"the approved parameters' record of {params.season - 1} is not the "
                       "snapshot's choice for that season")  # fmt: skip
    return out


def snapshot_dir(version: str, root: Path | None = None) -> Path:
    from twm.modules.regression_watch.production import artifact_dir

    return artifact_dir(root) / f"backtest-{version}"


def write_snapshot(
    frames: dict[str, pl.DataFrame], version: str, root: Path | None = None
) -> dict[str, Any]:
    """Write the frames as ``backtest-<version>/<table>.parquet`` (zstd, the Radar's level)
    and describe them for the pin ({table: SnapshotFile})."""
    from twm import pins
    from twm.config import ROOT

    base = root if root is not None else ROOT
    out_dir = snapshot_dir(version, base)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for table in TABLES:
        path = out_dir / f"{table}.parquet"
        tmp = path.with_name(path.name + ".tmp")
        frames[table].write_parquet(tmp, compression="zstd", compression_level=pins.PARQUET_LEVEL,
                                    statistics=False)  # fmt: skip
        tmp.replace(path)
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            rel = str(path)
        files[table] = pins.SnapshotFile(rel, pins.sha256_of(path), frames[table].height)
    return files


def load_snapshot(pin: Any, root: Path | None = None) -> dict[str, pl.DataFrame]:
    """The pinned snapshot (every file's sha256 checked BEFORE it is read, rows after), which
    must hang together: one outcome per row, every row's parameters listed."""
    from twm import pins

    if set(TABLES) - set(pin.backtest) or set(pin.backtest) - {*TABLES, PLAYER_XFP}:
        raise pins.PinError(
            f"the pin of {pin.module} has no frozen backtest lists: freeze them with `uv run twm "
            "regression freeze` on the owner's Mac (warehouse from 2006), review and commit"
        )
    frames = pins.read_snapshot(pin, TABLES, root)
    p, o, v = frames["predictions"], frames["outcomes"], frames["model_versions"]
    keys = ["season", "week", "entity_id"]
    if p.select(keys).join(o, on=keys, how="anti").height or o.height != p.height:
        raise pins.PinError("the frozen Regression Watch lists have rows without an outcome")
    if not set(p.get_column("model_version").unique().to_list()) <= set(v["model_version"]):
        raise pins.PinError("the frozen Regression Watch lists name parameters they do not list")
    return frames


def freeze(
    db: Path | str, season: int, league: League, *, csv_path: Path, root: Path | None = None,
    path: Path | None = None, progress: Progress | None = None,
) -> Any:  # fmt: skip
    """Build the snapshot for the approved parameters of ``season``, refuse unless it matches
    ``csv_path`` and the parameters, write it and add it to their pin (the parameters file and
    every other pin keep their bytes); with own parameters the player pages' own-xFP history
    too (:func:`freeze_player_xfp`, step PXFP). Returns the new pin."""
    from dataclasses import replace

    from twm import pins
    from twm.modules.regression_watch import production as rprod

    params, pin = rprod.load_pinned_params(season, path=path, root=root)
    frames = build_snapshot(db, season, league, xfp_source=params.xfp_source, progress=progress)
    if params.xfp_source == "own":  # the folds are current now: nothing is fit twice
        pxfp = build_player_xfp(db, season, progress=progress)
    problems = snapshot_mismatches(frames, csv_path, league, params)
    if problems:
        raise rprod.RegressionProductionError(
            f"the frozen lists disagree with {csv_path} or the approved parameters in "
            f"{len(problems)} places (e.g. {problems[0]}): rerun `uv run twm regression "
            "backtest` on the same warehouse"
        )
    files = write_snapshot(frames, params.model_version, root)
    if params.xfp_source == "own":
        files[PLAYER_XFP] = write_player_xfp(pxfp, params.model_version, root)
    seen = sorted(int(s) for s in frames["model_versions"].get_column("test_season").to_list())
    new = replace(pin, backtest=files, backtest_seasons=f"{seen[0]}-{seen[-1]}")
    current = pins.read_pins(path)
    current[rprod.PIN_KEY] = new
    pins.write_pins(current, path)
    return new


# --------------------------------------------------------------------------------------
# The player pages' own xFP history (step PXFP, owner's decision of 2026-10-02)
# --------------------------------------------------------------------------------------

PLAYER_XFP = "player_xfp"  # twm.pins.SNAPSHOT_TABLES
PLAYER_XFP_COLUMNS = ("season", "week", "game_id", "gsis_id", "xfp", "xfp_ng", "points_ng",
                      "fpoe_ng")  # fmt: skip
PLAYER_XFP_KEYS = ("season", "week", "game_id", "gsis_id")
PLAYER_XFP_TOLERANCE = 1e-9  # re-building from the same folds must give the frozen values


def player_xfp_rows(frame: pl.DataFrame, first: int) -> pl.DataFrame:
    """The own-xFP player weeks of a D1 frame with the own xFP (:func:`.player_week.with_xfp`)
    from season ``first``: every frame row (``xfp`` NULL where ffopportunity has no row for
    the game, as the frame keeps it), all plays and without garbage time."""
    typed = [pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)]
    rows = frame.filter(pl.col("season") >= int(first)).select(*typed, *PLAYER_XFP_COLUMNS[2:])
    return no_negative_zero(rows.sort(PLAYER_XFP_KEYS))


def player_xfp_first_season() -> int:
    """The player pages' first season (``seasons.snaps_start`` in config/settings.yaml)."""
    from twm.config import settings

    return int(settings().seasons["snaps_start"])


def build_player_xfp(
    db: Path | str, season: int, *, xfp: pl.DataFrame | None = None,
    progress: Progress | None = None, xfp_dir: Path | None = None,
) -> pl.DataFrame:  # fmt: skip
    """The player pages' own-xFP history before the approved ``season``: every season from
    :func:`player_xfp_first_season` to ``season`` - 1, each from the walk-forward fold trained
    on the seasons before it (``xfp``: those per player-game values, default
    :func:`.xfp_source.history` with its folds in ``xfp_dir``: may FIT folds not current, so the
    owner's Mac only). Deterministic."""
    from twm.modules.regression_watch import xfp_source as xs
    from twm.modules.regression_watch.player_week import player_games_history

    first, last = player_xfp_first_season(), int(season) - 1
    if xfp is None:
        xfp = xs.history(db, last, xs.OWN, progress=progress, out_dir=xfp_dir)
    frame = player_games_history(db, list(range(first, last + 1)), xfp=xfp)
    return player_xfp_rows(frame, first)


def load_player_xfp(pin: Any, root: Path | None = None) -> pl.DataFrame:
    """The pin's frozen own-xFP player weeks (sha256 before reading, rows after): REQUIRED (no
    fallback to ffopportunity), one row per player-game, every season before the pin's from
    the player pages' first season."""
    from twm import pins

    if PLAYER_XFP not in pin.backtest:
        raise pins.PinError(
            f"the pin of {pin.module} has no frozen own-xFP player weeks ({PLAYER_XFP}): freeze "
            "them with `uv run twm regression freeze --player-xfp` on the owner's Mac (the own "
            "xFP folds and the warehouse from 2006), review and commit"
        )
    df = pins.read_snapshot(pin, (PLAYER_XFP,), root)[PLAYER_XFP]
    if tuple(df.columns) != PLAYER_XFP_COLUMNS or df.select("game_id", "gsis_id").is_duplicated(
    ).any():  # fmt: skip
        raise pins.PinError(f"the {pin.module} pin's {PLAYER_XFP} is not one row per player-game")
    want = list(range(player_xfp_first_season(), int(pin.season)))
    if sorted(df.get_column("season").unique().to_list()) != want:
        raise pins.PinError(f"the {pin.module} pin's {PLAYER_XFP} does not cover {want[0]}-"
                            f"{want[-1]} (the seasons before {pin.season})")  # fmt: skip
    return df


def player_xfp_mismatches(frozen: pl.DataFrame, rebuilt: pl.DataFrame) -> list[str]:
    """Where a re-build disagrees with the frozen player weeks ([] = the same player-games and
    NULL pattern, every number within :data:`PLAYER_XFP_TOLERANCE`)."""
    keys = list(PLAYER_XFP_KEYS)
    a, b = frozen.sort(keys), rebuilt.select(PLAYER_XFP_COLUMNS).sort(keys)
    if a.height != b.height or not a.select(keys).equals(b.select(keys)):
        return [f"the re-build has other player-games ({b.height:,} rows, frozen {a.height:,})"]
    problems = []
    for c in PLAYER_XFP_COLUMNS[4:]:
        if not a[c].is_null().equals(b[c].is_null()):
            problems.append(f"{c}: the re-build is NULL in other rows")
            continue
        diff = float((a[c] - b[c]).abs().max() or 0.0)  # type: ignore[arg-type]
        if diff > PLAYER_XFP_TOLERANCE:
            problems.append(f"{c}: the re-build differs by up to {diff:.3g}")
    return problems


def write_player_xfp(rows: pl.DataFrame, version: str, root: Path | None = None) -> Any:
    """Write ``backtest-<version>/player_xfp.parquet`` (zstd, the snapshot's level; atomic) and
    describe it for the pin (a SnapshotFile)."""
    from twm import pins
    from twm.config import ROOT

    base = root if root is not None else ROOT
    path = snapshot_dir(version, base) / f"{PLAYER_XFP}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    rows.select(PLAYER_XFP_COLUMNS).write_parquet(
        tmp, compression="zstd", compression_level=pins.PARQUET_LEVEL, statistics=False
    )
    tmp.replace(path)
    try:
        rel = path.relative_to(base).as_posix()
    except ValueError:
        rel = str(path)
    return pins.SnapshotFile(rel, pins.sha256_of(path), rows.height)


def freeze_player_xfp(
    db: Path | str, season: int, *, root: Path | None = None, path: Path | None = None,
    progress: Progress | None = None, xfp_dir: Path | None = None,
) -> Any:  # fmt: skip
    """Freeze the player pages' own-xFP history (:func:`build_player_xfp`) into the approved
    ``season``'s pin snapshot (the parameters, the backtest lists and every other pin keep
    their bytes). The owner's Mac only; the scheduled job reads it. Returns the new pin."""
    from dataclasses import replace

    from twm import pins
    from twm.modules.regression_watch import production as rprod

    params, pin = rprod.load_pinned_params(season, path=path, root=root)
    if params.xfp_source != "own":
        raise rprod.RegressionProductionError(
            f"the approved parameters use the {params.xfp_source} xFP: the player pages' own "
            "xFP history is frozen only with own parameters"
        )
    rows = build_player_xfp(db, season, progress=progress, xfp_dir=xfp_dir)
    file = write_player_xfp(rows, params.model_version, root)
    new = replace(pin, backtest={**pin.backtest, PLAYER_XFP: file})
    current = pins.read_pins(path)
    current[rprod.PIN_KEY] = new
    pins.write_pins(current, path)
    return new


def rebuild_player_xfp(
    db: Path | str | None, season: int, *, out_dir: Path | None = None
) -> pl.DataFrame | None:
    """The player pages' own-xFP history re-built from the folds ALREADY on disk (their saved
    inputs and predictions; nothing is fit) and the warehouse, or None when either is missing
    (the scheduled job and CI have neither: they check the sha256 and rows only)."""
    from twm.config import ROOT
    from twm.modules.regression_watch import own_xfp as ox

    if db is None or not Path(db).exists():
        return None
    xfp = ox.saved_player_games(out_dir if out_dir is not None else ROOT / ox.OUT_DIR,
                                int(season) - 1)  # fmt: skip
    return None if xfp is None else build_player_xfp(db, season, xfp=xfp)
