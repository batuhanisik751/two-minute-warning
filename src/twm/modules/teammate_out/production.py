"""The frozen Teammate-out table (docs/teammate_out.md, "Frozen and pinned").

Built once on the owner's Mac from the warehouse (``twm teammate_out build``), approved with
``twm teammate_out pin`` and committed: one JSON file
``artifacts/production_models/teammate_out/alloc-<16 hex>.json`` holding the allocation cells
(fit on every season before the pinned one), the PPR-per-opportunity position means, the
walk-forward backtest of every candidate, the chosen candidate (the fixed rule's, unless the
pin was explicitly overridden: ``chosen_by``), the 80% range cells and their coverage, and the
event counts. The pin (config/production_models.yaml, key ``teammate_out``, ``model: alloc``)
names it with its sha256; the nightly runner checks it before reading and never rebuilds it.
``twm model check teammate_out`` (and ``all``) also compares it with reports/teammate_out/.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from twm.modules.teammate_out import history as hs
from twm.modules.teammate_out import table as tb

MODULE = "teammate_out"
PIN_KEY = "teammate_out"
MODEL = "alloc"  # a JSON allocation table, nothing pickled
LABEL = "teammate_points"
SPEC_FORMAT = 1
ARTIFACT_SUBDIR = "teammate_out"
DECIMALS = 6
TOLERANCE = 1.01e-6
REPORT_DIR = Path("reports") / "teammate_out"
REPORTS = ("backtest", "allocation", "ranges", "coverage", "events")
REPORT_COLUMNS = {  # the JSON stores rows with sorted keys: the reports' column order
    "backtest": ("candidate", "season", "n", "events", "mae_carry", "mae_target", "mae_points",
                 "top_hit", "chosen"),
    "allocation": ("out_pos", "role", "position", "n", "carry", "target", "n_group",
                   "carry_group", "target_group"),
    "ranges": ("position", "band", "n", "lo", "hi"),
    "coverage": ("position", "n", "below", "above", "inside", "coverage"),
    "events": ("out_pos", "sat", "kept", "no_roster_row", "gone_status", "events", "single",
               "multi", "teammate_rows"),
}  # fmt: skip
RULE = ("from 'nothing', the next candidate (pro_rata, group, role) replaces the choice only "
        f"if its pooled PPR-points MAE is lower and lower in at least {tb.SEASON_WINS_NEEDED} "
        "test seasons")  # fmt: skip


class TeammateOutProductionError(ValueError):
    """The table cannot be built, approved or read."""


def _r(x: Any) -> Any:
    return round(x, DECIMALS) if isinstance(x, float) else x


def records(df: pl.DataFrame) -> list[dict[str, Any]]:
    return [{k: _r(v) for k, v in row.items()} for row in df.iter_rows(named=True)]


def event_counts(cands: pl.DataFrame, mates: pl.DataFrame) -> pl.DataFrame:
    """Per absent position (and 'all'): starters who sat (candidates), kept as events, left
    out (no roster row on the team / a gone status), team-game events with that position
    out (single-starter and with 2+ out) and teammate rows."""
    c = cands.with_columns(pl.col("position").alias("out_pos"))
    ev = mates.unique(["season", "team", "gi"]).explode("out_pos")
    tm = mates.explode("out_pos")
    parts = []
    for pos in [*hs.POSITIONS, tb.ALL]:
        cp = c if pos == tb.ALL else c.filter(pl.col("out_pos") == pos)
        e = ev if pos == tb.ALL else ev.filter(pl.col("out_pos") == pos)
        e = e.unique(["season", "team", "gi"])
        t = tm if pos == tb.ALL else tm.filter(pl.col("out_pos") == pos)
        t = t.unique(["season", "team", "gi", "gsis_id"])
        parts.append({
            "out_pos": pos, "sat": cp.height,
            "kept": cp.filter(pl.col("with_team").fill_null(False)).height,
            "no_roster_row": cp.filter(pl.col("with_team").is_null()).height,
            "gone_status": cp.filter(~pl.col("with_team").fill_null(True)).height,
            "events": e.height, "single": e.filter(pl.col("n_out") == 1).height,
            "multi": e.filter(pl.col("n_out") > 1).height, "teammate_rows": t.height,
        })  # fmt: skip
    return pl.DataFrame(parts)


@dataclass(frozen=True)
class Built:
    season: int
    selection: tb.Selection
    fitted: tb.Fitted
    ranges: pl.DataFrame
    coverage: pl.DataFrame
    events: pl.DataFrame
    chosen: str  # the rule's choice unless overridden (content: chosen_by)


def build(
    cands: pl.DataFrame, mates: pl.DataFrame, season: int, candidate: str | None = None
) -> Built:
    """Backtest every candidate walk-forward, choose by the fixed rule (``candidate``: an
    explicit override), fit the cells on every season before ``season`` and the ranges on
    the chosen candidate's walk-forward misses."""
    if candidate is not None and candidate not in tb.CANDIDATES:
        raise TeammateOutProductionError(f"unknown candidate {candidate!r}: {tb.CANDIDATES}")
    mates = mates.filter(pl.col("season") < season)
    cands = cands.filter(pl.col("season") < season)
    tests = tuple(s for s in tb.TEST_SEASONS if s < season)
    if not tests:
        raise TeammateOutProductionError(f"no test season before {season}")
    sel = tb.select(tb.backtest(mates, tests))
    chosen = candidate or sel.chosen
    wf = tb.walk_forward(mates, chosen, tests)
    return Built(int(season), sel, tb.fit(mates), tb.range_cells(wf), tb.coverage(wf, tests),
                 event_counts(cands, mates), chosen)  # fmt: skip


def content(b: Built, reason: str | None = None) -> dict[str, Any]:
    """The JSON's content (floats with 6 decimals). ``chosen_by`` is 'rule' when the rule's
    choice is used, else ``reason`` (an override must say who decided it and why)."""
    chosen = b.chosen
    if chosen != b.selection.chosen and not (reason or "").strip():
        raise TeammateOutProductionError(
            f"{chosen!r} overrides the rule's {b.selection.chosen!r}: give the reason"
        )
    chosen_by = "rule" if chosen == b.selection.chosen else str(reason).strip()
    tests = [int(s) for s in tb.TEST_SEASONS if s < b.season]
    return {
        "format": SPEC_FORMAT, "module": MODULE, "season": b.season,
        "train_seasons": f"{hs.FIRST_SEASON}-{b.season - 1}", "test_seasons": tests,
        "candidates": list(tb.CANDIDATES), "rule": RULE, "rule_choice": b.selection.chosen,
        "path": list(b.selection.path), "chosen": chosen,
        "chosen_by": chosen_by,
        "event_rule": {
            "window": hs.WINDOW, "min_window_games": hs.MIN_WINDOW_GAMES,
            "rb_carry_share": hs.RB_CARRY_SHARE, "target_share": hs.TARGET_SHARE,
            "min_base_games": hs.MIN_BASE_GAMES, "left_statuses": list(hs.LEFT_STATUSES),
            "role_cap": dict(hs.ROLE_CAP), "positions": list(hs.POSITIONS),
        },
        "pseudo_count": tb.PSEUDO_COUNT, "ppo_pseudo": tb.PPO_PSEUDO,
        "carry_out": list(tb.CARRY_OUT),
        "ppo_mean": {k: _r(v) for k, v in sorted(b.fitted.ppo_mean.items())},
        "cells": records(b.fitted.cells),
        "backtest": records(b.selection.metrics),
        "ranges": {"level": tb.LEVEL, "q_lo": tb.Q_LO, "q_hi": tb.Q_HI,
                   "point_bands": list(tb.POINT_BANDS), "min_misses": tb.MIN_MISSES,
                   "cells": records(b.ranges)},
        "coverage": records(b.coverage),
        "events": records(b.events),
    }  # fmt: skip


def version_of(c: Mapping[str, Any]) -> str:
    """``alloc-<16 hex>``: :func:`twm.predictions.model_version` of the content."""
    from twm.predictions import model_version

    seasons = range(int(c["train_seasons"][:4]), int(c["train_seasons"][-4:]) + 1)
    return model_version(module=MODULE, model=MODEL, label=LABEL, features=["role"],
                         params=dict(c), training_seasons=list(seasons),
                         test_season=int(c["season"]), dataset_hash="")  # fmt: skip


def spec_json(c: Mapping[str, Any]) -> str:
    return json.dumps({**c, "model_version": version_of(c)}, sort_keys=True, indent=1) + "\n"


@dataclass(frozen=True)
class Spec:
    """A read (pinned) table: :meth:`predict` is all the live list uses."""

    content: dict[str, Any]

    @property
    def model_version(self) -> str:
        return str(self.content["model_version"])

    @property
    def season(self) -> int:
        return int(self.content["season"])

    @property
    def chosen(self) -> str:
        return str(self.content["chosen"])

    def cells(self) -> pl.DataFrame:
        return pl.DataFrame(self.content["cells"], infer_schema_length=None)

    def range_cells(self) -> pl.DataFrame:
        return pl.DataFrame(self.content["ranges"]["cells"], infer_schema_length=None)

    def predict(self, mates: pl.DataFrame) -> pl.DataFrame:
        """The chosen candidate's pred_* columns and 80% range, plus the allocation table's
        shares (``alloc_carry_share`` / ``alloc_target_share``: the 'role' cells, the past
        average shift, shown whatever the chosen candidate)."""
        ppo = {k: float(v) for k, v in self.content["ppo_mean"].items()}
        cells = self.cells()
        out = tb.with_ranges(tb.predict(mates, self.chosen, cells, ppo), self.range_cells())
        role = tb.predict(mates, "role", cells, ppo).select(
            "season", "team", "gi", "gsis_id",
            pl.col("pred_carry_share").alias("alloc_carry_share"),
            pl.col("pred_target_share").alias("alloc_target_share"),
        )  # fmt: skip
        return out.join(role, on=["season", "team", "gi", "gsis_id"], how="left")

    def describe(self) -> str:
        c = self.content
        cov = next((r["coverage"] for r in c["coverage"] if r["position"] == tb.ALL), None)
        bt = {r["candidate"]: r["mae_points"] for r in c["backtest"] if r["season"] == tb.ALL}
        maes = ", ".join(f"{k} {v:.3f}" for k, v in bt.items())
        cov_s = "-" if cov is None else f"{cov:.1%}"
        return (f"chosen {c['chosen']} ({c['chosen_by']}; rule path {' -> '.join(c['path'])}), "
                f"fit on {c['train_seasons']}, {len(c['cells'])} cells; points MAE {maes}; "
                f"80% range coverage {cov_s}")  # fmt: skip


def read_spec(path: Path) -> Spec:
    """The JSON file; the version inside must equal the hash of the rest (else refused)."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise TeammateOutProductionError(f"{path}: unreadable ({e})") from e
    if data.get("format") != SPEC_FORMAT or data.get("module") != MODULE:
        raise TeammateOutProductionError(f"{path}: not a teammate_out table (format {SPEC_FORMAT})")
    body = {k: v for k, v in data.items() if k != "model_version"}
    if data.get("model_version") != version_of(body):
        raise TeammateOutProductionError(
            f"{path}: holds {data.get('model_version')}, its content hashes to {version_of(body)}"
        )
    return Spec(data)


def report_frames(c: Mapping[str, Any]) -> dict[str, pl.DataFrame]:
    """The committed reports' rows from the JSON content (reports/teammate_out/*.csv)."""

    def f(rows: list[dict[str, Any]], name: str) -> pl.DataFrame:
        return pl.DataFrame(rows, infer_schema_length=None).select(REPORT_COLUMNS[name])

    bt = pl.DataFrame(c["backtest"], infer_schema_length=None).with_columns(
        (pl.col("candidate") == c["chosen"]).alias("chosen"))  # fmt: skip
    return {"backtest": bt.select(REPORT_COLUMNS["backtest"]),
            "allocation": f(c["cells"], "allocation"),
            "ranges": f(c["ranges"]["cells"], "ranges"),
            "coverage": f(c["coverage"], "coverage"),
            "events": f(c["events"], "events")}  # fmt: skip


def _cell(x: Any) -> str:
    if x is None:
        return "-"
    return f"{x:.{DECIMALS}f}" if isinstance(x, float) else str(x)


def report_markdown(c: Mapping[str, Any]) -> str:
    """reports/teammate_out/report.md: the CSVs as markdown tables (the model card cites it)."""
    body = {k: v for k, v in c.items() if k != "model_version"}
    lines = [f"# Teammate out: {version_of(body)}", "",
             f"Chosen: {c['chosen']} ({c['chosen_by']}; the rule's path "
             f"{' -> '.join(c['path'])}); cells fit on {c['train_seasons']}; pseudo-count "
             f"{c['pseudo_count']:g}. Rule: {c['rule']}.", ""]  # fmt: skip
    for name, df in report_frames(c).items():
        lines += [f"## {name}", "", "| " + " | ".join(df.columns) + " |",
                  "|" + "---|" * len(df.columns)]  # fmt: skip
        lines += ["| " + " | ".join(_cell(x) for x in row) + " |" for row in df.iter_rows()]
        lines.append("")
    return "\n".join(lines)


def write_reports(c: Mapping[str, Any], directory: Path) -> list[Path]:
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
    """Where the JSON disagrees with the committed CSVs and report.md ([] = they agree)."""
    problems = []
    md = directory / "report.md"
    if not md.exists() or md.read_text() != report_markdown(c):
        problems.append(f"{md} is missing or differs from the table")
    for name, want in report_frames(c).items():
        path = directory / f"{name}.csv"
        if not path.exists():
            problems.append(f"{path} is missing")
            continue
        got = pl.read_csv(path, infer_schema_length=0)
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
    """Refuse unless the content reproduces ``report_dir``'s reports; write
    ``artifacts/production_models/teammate_out/<version>.json`` (never over a different file
    of the same name) and pin it (other pins keep theirs). Returns the new pin."""
    from datetime import UTC, datetime

    from twm import pins
    from twm.config import ROOT

    problems = report_mismatches(c, report_dir)
    if problems:
        raise TeammateOutProductionError(
            f"the table disagrees with {report_dir} in {len(problems)} places (e.g. "
            f"{problems[0]}): run `uv run twm teammate_out build` on the same warehouse first"
        )
    base = root if root is not None else ROOT
    text, version = spec_json(c), version_of(c)
    target = artifact_dir(base) / f"{version}.json"
    if target.exists() and target.read_text() != text:
        raise TeammateOutProductionError(f"{target} exists with other bytes; not overwritten")
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
                   backtest_seasons=f"{c['test_seasons'][0]}-{c['test_seasons'][-1]}",
                   model=MODEL)  # fmt: skip
    current = pins.read_pins(path)
    current[PIN_KEY] = pin
    pins.write_pins(current, path)
    return pin


def load_pinned(season: int, *, path: Path | None = None, root: Path | None = None):
    """(the approved :class:`Spec`, its pin); :class:`twm.pins.PinError` when the pin is
    missing, is not an alloc pin, scores another season, or its file is missing, not the
    approved bytes (sha256, checked before the file is read) or another version."""
    from twm import pins

    pin = pins.get_pin(PIN_KEY, path)
    if pin.model != MODEL:
        raise pins.PinError(f"the pin of {PIN_KEY} says model {pin.model!r}, not {MODEL!r}")
    if pin.season != int(season):
        raise pins.PinError(
            f"the approved {PIN_KEY} table scores season {pin.season}, not {season}: build and "
            "approve it for this season (`uv run twm teammate_out pin`), review and commit"
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
    except TeammateOutProductionError as e:
        raise pins.PinError(str(e)) from e
    if spec.model_version != pin.model_version or spec.season != int(season):
        raise pins.PinError(f"{pin.file} holds {spec.model_version}, the pin says "
                            f"{pin.model_version}")  # fmt: skip
    return spec, pin
