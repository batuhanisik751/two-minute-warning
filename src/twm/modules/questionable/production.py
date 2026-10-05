"""The frozen Questionable table (docs/questionable.md, "Frozen and pinned").

Built once on the owner's Mac from the warehouse (``twm questionable build``), approved with
``twm questionable pin`` and committed: one JSON file
``artifacts/production_models/questionable/lookup-<16 hex>.json`` holding the chosen
grouping's cells (fit on every season before the pinned one), the walk-forward backtest of
every grouping tried, the calibration, the "if he plays" buckets and the rules they used.
The pin (config/production_models.yaml, key ``questionable``, ``model: lookup``) names it with
its sha256. The nightly runner never rebuilds it: it checks the sha256 before reading
(:func:`load_pinned`) and only applies the cells. ``twm model check questionable`` (and
``all``) also checks the file's backtest, calibration and buckets against the committed
reports/questionable/*.csv (the published track record).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.questionable import history as hs
from twm.modules.questionable import plays as pp
from twm.modules.questionable import table as tb

MODULE = "questionable"
PIN_KEY = "questionable"
MODEL = "lookup"  # the pin's `model`: a JSON lookup table, nothing pickled
LABEL = "played"
SPEC_FORMAT = 1
ARTIFACT_SUBDIR = "questionable"
DECIMALS = 6
TOLERANCE = 1.01e-6  # the report CSVs and the JSON print 6 decimals
REPORT_DIR = Path("reports") / "questionable"
REPORTS = ("backtest", "calibration", "if_plays")  # reports/questionable/<name>.csv
CALIBRATION_COLUMNS = ("bucket", "n", "predicted", "actual")
PLAYS_COLUMNS = ("report_status", "key", "value", "n", "median", "dud_rate", "matched",
                 "healthy_rows", "healthy_median", "healthy_dud_rate")  # fmt: skip


class QuestionableProductionError(ValueError):
    """The table cannot be built, approved or read."""


def _r(x: Any) -> Any:
    if isinstance(x, float):
        return round(x, DECIMALS)
    return x


def _records(df: pl.DataFrame) -> list[dict[str, Any]]:
    return [{k: _r(v) for k, v in row.items()} for row in df.iter_rows(named=True)]


@dataclass(frozen=True)
class Built:
    """Everything the JSON holds, as frames (from :func:`build`)."""

    season: int
    selection: tb.Selection
    table: tb.Table
    calibration: pl.DataFrame
    if_plays: pl.DataFrame
    statuses: pl.DataFrame  # rows and played share per game status, Out included


def build(tagged: pl.DataFrame, played: pl.DataFrame, season: int) -> Built:
    """Select the grouping walk-forward, fit it on every season before ``season`` and compute
    the calibration and the "if he plays" buckets (history rows of seasons < ``season``)."""
    tagged = tagged.filter(pl.col("season") < season)
    played = played.filter(pl.col("season") < season)
    tests = tuple(s for s in tb.TEST_SEASONS if s < season)
    if not tests:
        raise QuestionableProductionError(f"no test season before {season}")
    sel = tb.select(tagged, tests)
    table = tb.fit(tagged, sel.chosen)
    cal = tb.calibration(tb.walk_forward(tagged, sel.chosen, tests))
    return Built(int(season), sel, table, cal, pp.bucket_table(tagged, played),
                 hs.out_counts(tagged))  # fmt: skip


def cells_frame(table: tb.Table) -> pl.DataFrame:
    """Every level's cells in one frame: depth, report_status, the keys (null above their
    depth), n, played, rate."""
    out = []
    for depth, cells in enumerate(table.levels):
        df = cells.with_columns(pl.lit(depth).alias("depth"))
        for k in table.keys[depth:]:
            df = df.with_columns(pl.lit(None).alias(k))
        out.append(df.select("depth", "report_status", *table.keys, "n", "played", "rate"))
    return pl.concat([d.with_columns(pl.col(k).cast(pl.String) for k in table.keys) for d in out])


def content(b: Built) -> dict[str, Any]:
    """The JSON's content (sorted keys when written; floats with 6 decimals)."""
    seasons = sorted(set(range(hs.FIRST_SEASON, b.season)))
    return {
        "format": SPEC_FORMAT,
        "module": MODULE,
        "season": b.season,
        "train_seasons": f"{seasons[0]}-{seasons[-1]}",
        "test_seasons": [int(s) for s in tb.TEST_SEASONS if s < b.season],
        "grouping": b.selection.chosen,
        "keys": list(b.table.keys),
        "path": list(b.selection.path),
        "with_practice": b.selection.with_practice,
        "practice_dropped": b.selection.practice_dropped,
        "check_season": tb.CHECK_SEASON,
        "pseudo_count": b.table.pseudo_count,
        "rule": f"lower pooled log loss and Brier, and lower log loss in at least "
        f"{tb.SEASON_WINS_NEEDED} of the test seasons",
        "statuses": list(hs.MODELLED),
        "positions": list(hs.POSITIONS),
        "body_parts": list(hs.BODY_PARTS),
        "cells": _records(cells_frame(b.table)),
        "backtest": _records(b.selection.metrics),
        "calibration": _records(b.calibration),
        "if_plays": _records(b.if_plays),
        "status_counts": _records(b.statuses),
        "plays_rules": {
            "min_prior_games": pp.MIN_PRIOR_GAMES,
            "min_prior_ppg": pp.MIN_PRIOR_PPG,
            "dud": pp.DUD,
            "ppg_bands": list(pp.PPG_BANDS),
            "min_bucket_n": pp.MIN_BUCKET_N,
            "by": pp.BY,
        },  # fmt: skip
    }


def version_of(c: Mapping[str, Any]) -> str:
    """``lookup-<16 hex>``: :func:`twm.predictions.model_version` of the content."""
    from twm.predictions import model_version

    seasons = range(int(c["train_seasons"][:4]), int(c["train_seasons"][-4:]) + 1)
    return model_version(module=MODULE, model=MODEL, label=LABEL, features=c["keys"],
                         params=dict(c), training_seasons=list(seasons),
                         test_season=int(c["season"]), dataset_hash="")  # fmt: skip


def spec_json(c: Mapping[str, Any]) -> str:
    """The file's exact text: the content plus its version, sorted keys."""
    return json.dumps({**c, "model_version": version_of(c)}, sort_keys=True, indent=1) + "\n"


def as_keys(rows: pl.DataFrame, keys: tuple[str, ...]) -> pl.DataFrame:
    """The key columns as strings, as the JSON stores them (booleans: 'true' / 'false')."""
    return rows.with_columns(pl.col(k).cast(pl.String) for k in keys)


@dataclass(frozen=True)
class Spec:
    """A read (pinned) table: :meth:`chance` and :meth:`if_plays` are all the live list uses."""

    content: dict[str, Any]

    @property
    def model_version(self) -> str:
        return str(self.content["model_version"])

    @property
    def season(self) -> int:
        return int(self.content["season"])

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self.content["keys"])

    def table(self) -> tb.Table:
        cells = pl.DataFrame(self.content["cells"], infer_schema_length=None)
        levels = []
        for depth in range(len(self.keys) + 1):
            cols = ["report_status", *self.keys[:depth]]
            lv = cells.filter(pl.col("depth") == depth).select(*cols, "n", "played", "rate")
            levels.append(as_keys(lv, self.keys[:depth]))
        return tb.Table(self.content["grouping"], self.keys, tuple(levels),
                        float(self.content["pseudo_count"]))  # fmt: skip

    def chance(self, rows: pl.DataFrame) -> pl.Series:
        """Each row's chance he plays (rows need report_status and the grouping's keys)."""
        return tb.predict(self.table(), as_keys(rows, self.keys))

    def if_plays(self, status: str, value: str) -> dict[str, Any] | None:
        """The bucket's "if he plays" line (``value``: his missed_prev as 'true' / 'false'):
        its own when it has at least min_bucket_n rows, else its status's overall line, else
        None."""
        need = int(self.content["plays_rules"]["min_bucket_n"])
        rows = {(r["report_status"], r["value"]): r for r in self.content["if_plays"]}
        for key in ((status, value), (status, pp.ALL)):
            r = rows.get(key)
            if r is not None and int(r["n"]) >= need:
                return dict(r)
        return None

    def describe(self) -> str:
        c = self.content
        cal = ", ".join(f"{r['bucket']} {r['n']}" for r in c["calibration"])
        path = " -> ".join(c["path"])
        return (
            f"grouping {c['grouping']} (path {path}), K={c['pseudo_count']:g}, fit on "
            f"{c['train_seasons']}, {len(c['cells'])} cells; calibration rows: {cal}"
        )


def read_spec(path: Path) -> Spec:
    """The JSON file; the version inside must equal the hash of the rest (else refused)."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise QuestionableProductionError(f"{path}: unreadable ({e})") from e
    if data.get("format") != SPEC_FORMAT or data.get("module") != MODULE:
        raise QuestionableProductionError(
            f"{path}: not a questionable table (format {SPEC_FORMAT})"
        )
    body = {k: v for k, v in data.items() if k != "model_version"}
    if data.get("model_version") != version_of(body):
        raise QuestionableProductionError(
            f"{path}: holds {data.get('model_version')}, its content hashes to {version_of(body)}"
        )
    return Spec(data)


def report_frames(c: Mapping[str, Any]) -> dict[str, pl.DataFrame]:
    """The committed reports' rows from the JSON content (reports/questionable/*.csv)."""
    bt = pl.DataFrame(c["backtest"], infer_schema_length=None).with_columns(
        pl.col("grouping").map_elements(tb.title, return_dtype=pl.String).alias("title"),
        (pl.col("grouping") == c["grouping"]).alias("chosen"),
    )  # fmt: skip
    return {
        "backtest": bt.select(
            "grouping",
            "title",
            "chosen",
            "season",
            "n",
            "log_loss",
            "brier",
            "mean_p",
            "played_rate",
        ),  # fmt: skip
        "calibration": pl.DataFrame(c["calibration"], infer_schema_length=None).select(
            CALIBRATION_COLUMNS
        ),
        "if_plays": pl.DataFrame(c["if_plays"], infer_schema_length=None).select(PLAYS_COLUMNS),
    }


def _cell(x: Any) -> str:
    if x is None:
        return "-"
    return f"{x:.{DECIMALS}f}" if isinstance(x, float) else str(x)


def report_markdown(c: Mapping[str, Any]) -> str:
    """reports/questionable/report.md: the three CSVs as markdown tables (the model card cites
    it; the numbers are the CSVs' own)."""
    body = {k: v for k, v in c.items() if k != "model_version"}
    lines = [f"# Questionable outcomes: {version_of(body)}",
             "", f"Chosen grouping: {c['grouping']} (path {' -> '.join(c['path'])}); fit on "
             f"{c['train_seasons']}; pseudo-count {c['pseudo_count']:g}.", ""]  # fmt: skip
    for name, df in report_frames(c).items():
        lines += [f"## {name}", "", "| " + " | ".join(df.columns) + " |",
                  "|" + "---|" * len(df.columns)]  # fmt: skip
        lines += ["| " + " | ".join(_cell(x) for x in row) + " |" for row in df.iter_rows()]
        lines.append("")
    return "\n".join(lines)


def write_reports(c: Mapping[str, Any], directory: Path) -> list[Path]:
    """Write reports/questionable/{backtest,calibration,if_plays}.csv (6 decimals) and
    report.md (:func:`report_markdown`)."""
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name, df in report_frames(c).items():
        path = directory / f"{name}.csv"
        df.write_csv(path, float_precision=DECIMALS)
        out.append(path)
    md = directory / "report.md"
    md.write_text(report_markdown(c))
    return [*out, md]


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None or a == "" or b == "":
        return (a is None or a == "") and (b is None or b == "")
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower() == str(b).lower()
    try:
        return abs(float(a) - float(b)) <= TOLERANCE
    except (TypeError, ValueError):
        return str(a) == str(b)


def report_mismatches(c: Mapping[str, Any], directory: Path) -> list[str]:
    """Where the JSON disagrees with the committed CSVs ([] = they agree)."""
    problems = []
    md = directory / "report.md"
    if not md.exists() or md.read_text() != report_markdown(c):
        problems.append(f"{md} is missing or differs from the table")
    for name, want in report_frames(c).items():
        path = directory / f"{name}.csv"
        if not path.exists():
            problems.append(f"{path} is missing")
            continue
        got = pl.read_csv(path, infer_schema_length=0)  # every value as text
        if got.columns != want.columns or got.height != want.height:
            problems.append(f"{path}: {got.height} rows {got.columns}, the table has "
                            f"{want.height} rows {want.columns}")  # fmt: skip
            continue
        for i, (g, w) in enumerate(zip(got.iter_rows(), want.iter_rows(), strict=True)):
            bad = [col for col, x, y in zip(want.columns, g, w, strict=True) if not _same(x, y)]
            if bad:
                problems.append(f"{path} row {i + 1}: {', '.join(bad)} differ")
    return problems


def artifact_dir(root: Path | None = None) -> Path:
    from twm import pins
    from twm.config import ROOT

    return (root if root is not None else ROOT) / pins.ARTIFACT_DIR / ARTIFACT_SUBDIR


def approve(
    c: Mapping[str, Any],
    *,
    report_dir: Path,
    root: Path | None = None,
    path: Path | None = None,
    today: Any = None,
) -> Any:
    """Refuse unless the content reproduces ``report_dir``'s CSVs; write
    ``artifacts/production_models/questionable/<version>.json`` (never over a different file
    of the same name) and pin it (other pins keep theirs). Returns the new pin."""
    from datetime import UTC, datetime

    from twm import pins
    from twm.config import ROOT

    problems = report_mismatches(c, report_dir)
    if problems:
        raise QuestionableProductionError(
            f"the table disagrees with {report_dir} in {len(problems)} places (e.g. "
            f"{problems[0]}): run `uv run twm questionable build` on the same warehouse first"
        )
    base = root if root is not None else ROOT
    text = spec_json(c)
    version = version_of(c)
    target = artifact_dir(base) / f"{version}.json"
    if target.exists() and target.read_text() != text:
        raise QuestionableProductionError(f"{target} exists with other bytes; not overwritten")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text)
        tmp.replace(target)
    try:
        rel = target.relative_to(base).as_posix()
    except ValueError:
        rel = str(target)
    day = (today or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    pin = pins.Pin(PIN_KEY, int(c["season"]), version, rel, pins.sha256_of(target), day,
                   backtest_seasons=f"{hs.FIRST_SEASON}-{int(c['season']) - 1}",
                   model=MODEL)  # fmt: skip
    current = pins.read_pins(path)
    current[PIN_KEY] = pin
    pins.write_pins(current, path)
    return pin


def load_pinned(season: int, *, path: Path | None = None, root: Path | None = None):
    """(the approved :class:`Spec`, its pin); :class:`twm.pins.PinError` when the pin is
    missing, is not a lookup pin, scores another season, or its file is missing, not the
    approved bytes (sha256, checked before the file is read) or another version."""
    from twm import pins

    pin = pins.get_pin(PIN_KEY, path)
    if pin.model != MODEL:
        raise pins.PinError(f"the pin of {PIN_KEY} says model {pin.model!r}, not {MODEL!r}")
    if pin.season != int(season):
        raise pins.PinError(
            f"the approved {PIN_KEY} table scores season {pin.season}, not {season}: build and "
            "approve it for this season (`uv run twm questionable pin`), review and commit"
        )
    file = pin.path(root)
    if not file.exists():
        raise pins.PinError(f"the approved file is missing: {pin.file}")
    digest = pins.sha256_of(file)
    if digest != pin.sha256:
        raise pins.PinError(f"{pin.file} is not the approved file (sha256 {digest[:12]}..., the "
                            f"pin says {pin.sha256[:12]}...); it was not opened")  # fmt: skip
    try:
        spec = read_spec(file)
    except QuestionableProductionError as e:
        raise pins.PinError(str(e)) from e
    if spec.model_version != pin.model_version or spec.season != int(season):
        raise pins.PinError(f"{pin.file} holds {spec.model_version}, the pin says "
                            f"{pin.model_version}")  # fmt: skip
    return spec, pin
