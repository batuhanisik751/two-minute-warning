"""Benchmark of our fourth-down recommendations against the nfl4th R package (spec 8.4; G5).

For the graded fourth downs of one or two recent seasons (G3/G1b grades, stored under
data/decisions/graded/), :func:`export_states` writes the state columns nfl4th's
``add_4th_probs()`` documents, :func:`export_games` the games table it joins (lines, roof),
from our warehouse; ``scripts/benchmarks/nfl4th.R`` runs nfl4th on them (never downloading:
see the script's header) and :func:`join` puts its WP for go / field goal / punt and its
recommendation back on our rows by the play key. The report is
:mod:`twm.modules.decisions.benchmark_report`.

The rows benchmarked are our graded rows (no exclusion rule) that nfl4th itself evaluates:
regulation only and more than 15 s left in the game (nfl4th's ``prepare_nflfastr_data()``
filters for nflfastR input). nfl4th's models were fitted on nflfastR data that very likely
includes these seasons (in-sample for nfl4th, PROJECT_SPEC 6.3); ours never saw them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import polars as pl

KEYS = ("game_id", "play_id")
SEASONS = (2024, 2025)
FIRST_SEASON = 2014  # nfl4th's games table and load_4th_pbp start there
MIN_GAME_SECONDS = 15  # nfl4th evaluates game_seconds_remaining > 15 only
OPTIONS = ("go", "field_goal", "punt")

# nfl4th input column -> how we fill it (each verified against nfl4th 1.0.7's add_4th_probs help
# and prepare_df / get_*_wp source). nfl4th sets down = 4 itself and derives half / game
# seconds, spread and totals; no `runoff` column is passed (nfl4th's default 0).
STATE_COLUMNS: dict[str, str] = {
    "game_id": "play key (not used by nfl4th: dropped before its games join, kept for ours)",
    "play_id": "play key",
    "home_team": "stored row (warehouse fact_play)",
    "away_team": "stored row",
    "posteam": "stored row: the team with the ball",
    "type": "'reg' when season_type is REG, else 'post' (nfl4th's games-join key)",
    "season": "stored row",
    "qtr": "stored row (1-4; overtime is not benchmarked)",
    "quarter_seconds_remaining": "half_seconds_remaining - 900 in quarters 1 and 3, else "
    "half_seconds_remaining (nfl4th rebuilds half / game seconds from it)",
    "ydstogo": "stored row",
    "yardline_100": "stored row: yards to the opponent's end zone",
    "score_differential": "stored row: posteam score minus defteam score",
    "home_opening_kickoff": "1 when the home team kicked the game's first kickoff (our "
    "opening_kicker: defteam of the first quarter-1 kickoff row), else 0",
    "posteam_timeouts_remaining": "stored row (pre-snap)",
    "defteam_timeouts_remaining": "stored row (pre-snap)",
}
# games table (warehouse fact_game) -> nfl4th's get_games_file() transformations in the R script
GAME_COLUMNS = ("game_id", "season", "game_type", "week", "home_team", "away_team", "roof",
                "spread_line", "total_line")  # fmt: skip
# what nfl4th.R writes (add_4th_probs' documented outputs + ours)
NFL4TH_COLUMNS = ("nfl4th_version", "nfl4th_status", "fg_make_prob_bundled", "go_boost",
                  "first_down_prob", "wp_fail", "wp_succeed", "go_wp", "fg_make_prob",
                  "miss_fg_wp", "make_fg_wp", "fg_wp", "punt_wp", "nfl4th_recommended")  # fmt: skip


class Nfl4thUnavailableError(RuntimeError):
    """Rscript or the nfl4th package is missing: the benchmark is skipped, never failed."""


def work_dir() -> Path:
    """``data/decisions/benchmark/nfl4th``: exported CSVs, nfl4th's output and its cache."""
    from twm.config import ROOT

    return ROOT / "data" / "decisions" / "benchmark" / "nfl4th"


def r_script() -> Path:
    from twm.config import ROOT

    return ROOT / "scripts" / "benchmarks" / "nfl4th.R"


def select_rows(graded: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int]]:
    """Our graded fourth downs nfl4th evaluates, and the graded rows left out by reason."""
    g = graded.filter(pl.col("exclusion").is_null())
    ot = pl.col("qtr") > 4
    late = pl.col("game_seconds_remaining") <= MIN_GAME_SECONDS
    drops = {"overtime": g.filter(ot).height,
             "last_15_seconds": g.filter(~ot & late).height}  # fmt: skip
    return g.filter(~ot & ~late).sort(KEYS), drops


def export_states(rows: pl.DataFrame) -> pl.DataFrame:
    """The :data:`STATE_COLUMNS` of each row (see the mapping there), in that order."""
    hsr = pl.col("half_seconds_remaining")
    return rows.select(
        *KEYS, "home_team", "away_team", "posteam",
        pl.when(pl.col("season_type") == "REG").then(pl.lit("reg")).otherwise(pl.lit("post"))
        .alias("type"),
        "season", "qtr",
        pl.when(pl.col("qtr").is_in([1, 3])).then(hsr - 900).otherwise(hsr)
        .alias("quarter_seconds_remaining"),
        "ydstogo", "yardline_100", "score_differential",
        (pl.col("opening_kicker") == pl.col("home_team")).cast(pl.Int8)
        .alias("home_opening_kickoff"),
        "posteam_timeouts_remaining", "defteam_timeouts_remaining",
    )  # fmt: skip


def export_games(db: Path | str, seasons: Sequence[int]) -> pl.DataFrame:
    """The :data:`GAME_COLUMNS` of every game of ``seasons`` (warehouse, read-only)."""
    from twm.modules.decisions.grade_inputs import _query

    cols = ", ".join(GAME_COLUMNS)
    years = ", ".join(str(int(s)) for s in seasons)
    return _query(db, f"SELECT {cols} FROM w.fact_game WHERE season IN ({years}) "
                      "ORDER BY game_id")  # fmt: skip


def find_rscript(rscript: str | Path | None = None) -> str:
    """``rscript`` if given, else ``Rscript`` on PATH; :class:`Nfl4thUnavailableError` if none."""
    found = shutil.which(str(rscript)) if rscript is not None else shutil.which("Rscript")
    if not found:
        raise Nfl4thUnavailableError(
            f"Rscript not found ({rscript or 'PATH'}): the nfl4th benchmark is skipped "
            "(install R and the nfl4th package, or pass --rscript)")  # fmt: skip
    return found


SANDBOX = "/usr/bin/sandbox-exec"  # macOS: run R with every network call denied
NO_NETWORK = "(version 1)(allow default)(deny network*)"


def run_nfl4th(states: pl.DataFrame, games: pl.DataFrame, *, out_dir: Path | None = None,
               rscript: str | Path | None = None, tag: str = "bench",
               progress: Callable[[str], None] = print) -> pl.DataFrame:  # fmt: skip
    """Write the two CSVs, run nfl4th.R on them and return its output (one row per state).
    :class:`Nfl4thUnavailableError` when Rscript or nfl4th is missing; RuntimeError when R fails."""
    exe = find_rscript(rscript)
    d = out_dir if out_dir is not None else work_dir()
    d.mkdir(parents=True, exist_ok=True)
    paths = {k: d / f"{k}_{tag}.csv" for k in ("states", "games", "nfl4th")}
    states.write_csv(paths["states"])
    games.write_csv(paths["games"])
    paths["nfl4th"].unlink(missing_ok=True)
    cmd = [exe, str(r_script()), "--states", str(paths["states"]),
           "--games", str(paths["games"]), "--out", str(paths["nfl4th"]),
           "--cache", str(d / "cache")]  # fmt: skip
    if Path(SANDBOX).exists():
        cmd = [SANDBOX, "-p", NO_NETWORK, *cmd]
    progress(f"running nfl4th on {states.height} states (network denied: {cmd[0] == SANDBOX})")
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)
    for line in res.stderr.strip().splitlines()[-5:]:
        progress(f"  R: {line}")
    if res.returncode == 3:
        raise Nfl4thUnavailableError(f"the nfl4th R package is not installed for {exe}: "
                                     "the nfl4th benchmark is skipped")  # fmt: skip
    if res.returncode != 0 or not paths["nfl4th"].exists():
        raise RuntimeError(f"nfl4th.R failed (exit {res.returncode}): {res.stderr[-500:]}")
    return read_output(paths["nfl4th"])


TEXT_COLUMNS = ("nfl4th_version", "nfl4th_status", "nfl4th_recommended")


def read_output(path: Path) -> pl.DataFrame:
    """nfl4th.R's CSV with fixed types (columns that are empty in "fg_only" stay Float64)."""
    types = {c: pl.Utf8 if c in TEXT_COLUMNS else pl.Float64 for c in NFL4TH_COLUMNS}
    return pl.read_csv(path, schema_overrides={"game_id": pl.Utf8, **types})


MODEL_FILES = ("fd_model", "wp_model")  # nfl4th's two downloaded models (owner-approved)


def model_provenance(cache: Path) -> list[dict]:
    """The two model files in nfl4th's cache with their recorded provenance
    (``models.json``: url, bytes, sha256, download time); each file's size and sha256 are
    checked against the record (ValueError on a mismatch). Empty when the files are absent."""
    import hashlib

    d = cache / "R" / "nfl4th"
    rec_path = d / "models.json"
    rec = json.loads(rec_path.read_text()) if rec_path.exists() else {}
    out = []
    for name in MODEL_FILES:
        f = d / f"{name}.rds"
        if not f.exists():
            continue
        r = rec.get(name, {})
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        if r and (r.get("sha256") != sha or r.get("bytes") != f.stat().st_size):
            raise ValueError(f"{f} does not match its recorded size / sha256 in {rec_path}")
        out.append({"file": f"{name}.rds", "bytes": f.stat().st_size, "sha256": sha,
                    "url": r.get("url"), "downloaded_utc": r.get("downloaded_utc")})  # fmt: skip
    return out


def join(rows: pl.DataFrame, nfl4th: pl.DataFrame) -> pl.DataFrame:
    """``rows`` with nfl4th's columns, matched 1:1 on the play key (ValueError otherwise)."""
    out = nfl4th.select(*KEYS, *[c for c in NFL4TH_COLUMNS if c in nfl4th.columns])
    out = out.with_columns(pl.col(k).cast(rows.schema[k]) for k in KEYS)
    if out.select(KEYS).is_duplicated().any():
        raise ValueError("nfl4th output has duplicate play keys")
    missing = rows.select(KEYS).join(out.select(KEYS), on=list(KEYS), how="anti").height
    extra = out.select(KEYS).join(rows.select(KEYS), on=list(KEYS), how="anti").height
    if missing or extra:
        raise ValueError(f"nfl4th output does not match the exported rows: {missing} missing, "
                         f"{extra} unknown")  # fmt: skip
    return rows.join(out, on=list(KEYS), how="left", validate="1:1")


def _best_kick(wp_fg: str, fg_ok: pl.Expr, wp_punt: str, punt_ok: pl.Expr) -> pl.Expr:
    """The higher WP of the kicks that exist (null when neither does)."""
    fg = pl.when(fg_ok).then(pl.col(wp_fg))
    pt = pl.when(punt_ok).then(pl.col(wp_punt))
    return pl.max_horizontal(fg, pt)


CAUSES = ("conversion model", "field-goal model", "punt (result distribution and its WP)",
          "WP model and clock assumptions (value of the states after the play)")  # fmt: skip


def compare(joined: pl.DataFrame) -> pl.DataFrame:
    """Per-row comparison columns (WP in points, 0-100): ``agree``; ``our_go_gain`` = WP(go)
    - WP(best kick that exists for us); ``their_go_gain`` = nfl4th's ``go_boost`` (its punt
    counts as 0 where it has none: the same as "best kick that exists"); their difference;
    component differences (ours - nfl4th); and, where the recommendations differ, the
    ``likely_cause``: the component whose difference moves WP the most (a heuristic)."""
    j = joined
    ours, theirs = pl.col("recommended"), pl.col("nfl4th_recommended")
    kick = _best_kick("wp_fg", pl.col("fg_available"), "wp_punt", pl.col("punt_available"))
    involved = {o: (ours == o) | (theirs == o) for o in OPTIONS}
    terms = [
        (pl.col("p_convert") - pl.col("first_down_prob")).abs()
        * (pl.col("wp_succeed") - pl.col("wp_fail")).abs(),
        pl.when(involved["field_goal"]).then((pl.col("p_make") - pl.col("fg_make_prob")).abs()
                                             * (pl.col("make_fg_wp") - pl.col("miss_fg_wp")).abs())
        .otherwise(0.0),
        pl.when(involved["punt"]).then((pl.col("wp_punt") - pl.col("punt_wp")).abs())
        .otherwise(0.0),
        ((pl.col("wp_go_success") - pl.col("wp_succeed")).abs()
         + (pl.col("wp_go_failure") - pl.col("wp_fail")).abs()) / 2,
    ]  # fmt: skip
    j = j.with_columns(
        agree=ours == theirs,
        our_go_gain=100 * (pl.col("wp_go") - kick),
        their_go_gain=pl.col("go_boost"),
        d_p_convert=pl.col("p_convert") - pl.col("first_down_prob"),
        d_p_make=pl.col("p_make") - pl.col("fg_make_prob_bundled"),
        d_wp_success=100 * (pl.col("wp_go_success") - pl.col("wp_succeed")),
        d_wp_failure=100 * (pl.col("wp_go_failure") - pl.col("wp_fail")),
        d_wp_go=100 * (pl.col("wp_go") - pl.col("go_wp")),
        d_wp_fg=100 * (pl.col("wp_fg") - pl.col("fg_wp")),
        d_wp_punt=100 * (pl.col("wp_punt") - pl.col("punt_wp")),
        **{f"_cause_{i}": t.fill_null(0.0).fill_nan(0.0) for i, t in enumerate(terms)},
    )
    parts = [f"_cause_{i}" for i in range(len(terms))]
    idx = pl.concat_list(parts).list.arg_max()
    cause = pl.when(~pl.col("agree")).then(
        idx.replace_strict(list(range(len(CAUSES))), list(CAUSES), return_dtype=pl.Utf8)
    )
    return j.with_columns(go_gain_diff=pl.col("our_go_gain") - pl.col("their_go_gain"),
                          likely_cause=cause).drop(parts)  # fmt: skip


def benchmark(db: Path | str, seasons: Sequence[int] = SEASONS, *,
              graded_dir: Path | None = None, out_dir: Path | None = None,
              rscript: str | Path | None = None,
              progress: Callable[[str], None] = print) -> tuple[pl.DataFrame, dict]:  # fmt: skip
    """Export, run nfl4th, join and compare ``seasons``; writes ``joined.parquet`` (the rows the
    report reads) and returns (rows, info). :class:`Nfl4thUnavailableError` when R or nfl4th
    is missing."""
    from twm.modules.decisions import grade as gr

    seasons = sorted({int(s) for s in seasons})
    if seasons and seasons[0] < FIRST_SEASON:
        raise ValueError(f"nfl4th covers {FIRST_SEASON} on (its games table); got {seasons}")
    rscript = find_rscript(rscript)  # skip before touching any data when R is missing
    graded = gr.load_graded("fourth_downs", seasons, out_dir=graded_dir)
    rows, drops = select_rows(graded)
    states, games = export_states(rows), export_games(db, seasons)
    if states.null_count().sum_horizontal().item():
        raise ValueError("a benchmarked row lacks an nfl4th input")
    d = out_dir if out_dir is not None else work_dir()
    tag = "_".join(str(s) for s in seasons)
    out = run_nfl4th(states, games, out_dir=d, rscript=rscript, tag=tag, progress=progress)
    joined = compare(join(rows, out))
    joined.write_parquet(d / "joined.parquet")
    status = sorted(set(joined.get_column("nfl4th_status").to_list()))
    version = sorted(set(joined.get_column("nfl4th_version").to_list()))
    info = {"seasons": seasons, "graded_rows": graded.filter(pl.col("exclusion").is_null()).height,
            "benchmarked": joined.height, "left_out": drops, "nfl4th_status": status,
            "nfl4th_version": version, "network_denied": Path(SANDBOX).exists(),
            "models": model_provenance(d / "cache")}  # fmt: skip
    (d / "info.json").write_text(json.dumps(info, indent=1, sort_keys=True) + "\n")
    progress(f"nfl4th {version}: {joined.height} rows joined ({status})")
    return joined, info


def load(out_dir: Path | None = None) -> tuple[pl.DataFrame, dict]:
    """The last run's ``joined.parquet`` and ``info.json`` (FileNotFoundError if none)."""
    d = out_dir if out_dir is not None else work_dir()
    return pl.read_parquet(d / "joined.parquet"), json.loads((d / "info.json").read_text())
